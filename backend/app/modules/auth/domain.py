"""auth 模块领域对象（纯业务对象，无 ORM 依赖）。

包含：
- PermissionsCalculator：BR-PERM-001 权限合并算法（撤销 > 授予 > 角色）
- generate_random_password：BR-PWD-004 16 位随机密码（满足 BR-PWD-001 + 1 个特殊字符）
"""

from __future__ import annotations

import secrets
import string


# ---------------------------------------------------------------------------
# 权限合并
# ---------------------------------------------------------------------------


def merge_permissions(
    role_scopes: set[str],
    grants: set[str],
    revokes: set[str],
) -> frozenset[str]:
    """计算用户最终生效权限。

    BR-PERM-001：effective(U) = (role_perms ∪ grants) - revokes
    优先级：撤销 > 授予 > 角色
    """
    return frozenset((role_scopes | grants) - revokes)


# ---------------------------------------------------------------------------
# 密码生成
# ---------------------------------------------------------------------------

_PASSWORD_SPECIAL = "!@#$%^&*-_=+"


def generate_random_password(length: int = 16) -> str:
    """生成满足 BR-PWD-001 + 1 个特殊字符的随机密码。

    保证密码至少包含：1 大写、1 小写、1 数字、1 特殊字符。
    """
    if length < 10:
        length = 10
    pools = {
        "upper": string.ascii_uppercase,
        "lower": string.ascii_lowercase,
        "digit": string.digits,
        "special": _PASSWORD_SPECIAL,
    }
    # 确保每类至少 1 个
    chars = [secrets.choice(p) for p in pools.values()]
    # 剩余位从全集随机
    full_pool = "".join(pools.values())
    chars.extend(secrets.choice(full_pool) for _ in range(length - len(chars)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
