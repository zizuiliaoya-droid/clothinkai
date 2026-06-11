"""JWT 编解码 + 密码哈希 + 黑名单。

业务规则参考 functional-design/business-rules.md：
- BR-PWD-002: bcrypt cost=12
- BR-TKN-001/002: access 30min / refresh 7d
- BR-TKN-004: pwd_iat 安全戳 + JWT 黑名单（兜底）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt
from passlib.context import CryptContext

from app.core.cache import cache
from app.core.config import settings
from app.core.exceptions import TokenExpiredError, TokenInvalidError

# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.BCRYPT_ROUNDS,
)


def hash_password(plain: str) -> str:
    return str(pwd_context.hash(plain))


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bool(pwd_context.verify(plain, hashed))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# JWT 编解码
# ---------------------------------------------------------------------------

_ALG = settings.JWT_ALGORITHM
_ACCESS_EXPIRE = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
_REFRESH_EXPIRE = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def encode_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    roles: list[str],
    actor_type: str = "user",
    must_change_password: bool = False,
    pwd_iat: datetime,
) -> tuple[str, str]:
    """签发 access_token。返回 (token, jti)。"""
    jti = uuid4().hex
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id) if tenant_id is not None else None,
        "roles": roles,
        "actor_type": actor_type,
        "must_change_password": must_change_password,
        "pwd_iat": pwd_iat.isoformat(),
        "iat": now,
        "exp": now + _ACCESS_EXPIRE,
        "jti": jti,
        "typ": "access",
    }
    token = jwt.encode(payload, settings.JWT_SECRET.get_secret_value(), algorithm=_ALG)
    return token, jti


def encode_refresh_token(
    *, user_id: UUID, tenant_id: UUID | None
) -> tuple[str, str, datetime]:
    """签发 refresh_token。返回 (token, jti, expires_at)。"""
    jti = uuid4().hex
    now = _now()
    expires_at = now + _REFRESH_EXPIRE
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id) if tenant_id is not None else None,
        "iat": now,
        "exp": expires_at,
        "jti": jti,
        "typ": "refresh",
    }
    token = jwt.encode(payload, settings.JWT_SECRET.get_secret_value(), algorithm=_ALG)
    return token, jti, expires_at


def decode_token(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    """解码并验证 JWT。失败抛 TokenExpiredError / TokenInvalidError。"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[_ALG],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError() from exc

    if expected_type and payload.get("typ") != expected_type:
        raise TokenInvalidError(f"期望 token 类型 {expected_type}，实际 {payload.get('typ')}")
    return payload


def decode_token_unverified(token: str) -> dict[str, Any] | None:
    """不验签解码（仅用于中间件提取上下文，真正鉴权仍走 decode_token）。"""
    try:
        return jwt.decode(token, options={"verify_signature": False})  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# JWT 黑名单（Redis，兜底机制）
# ---------------------------------------------------------------------------

_BLACKLIST_PREFIX = "jwt:blacklist:"


async def revoke_jti(jti: str, ttl_seconds: int) -> None:
    """把 jti 加入黑名单。TTL 应等于 token 剩余有效期。"""
    if ttl_seconds <= 0:
        return
    await cache.setex(f"{_BLACKLIST_PREFIX}{jti}", ttl_seconds, "1")


async def is_revoked(jti: str) -> bool:
    return await cache.exists(f"{_BLACKLIST_PREFIX}{jti}")


async def revoke_token(token_payload: dict[str, Any]) -> None:
    """根据 token payload 自动计算剩余 TTL 加入黑名单。"""
    jti = token_payload.get("jti")
    exp = token_payload.get("exp")
    if not jti or not exp:
        return
    now = int(_now().timestamp())
    ttl = int(exp) - now
    if ttl > 0:
        await revoke_jti(str(jti), ttl)
