"""逐项事务编排；仅创建使用领域行键，业务动作完全复用原服务。"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from uuid import UUID

from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AccountDisabledError, AppException
from app.modules.auth.models import User
from app.modules.negotiation.bulk_schemas import (
    BulkAction,
    BulkActionItem,
    BulkActionRequest,
    BulkCreateRequest,
    BulkItemResult,
    BulkLookupRequest,
    BulkResponse,
    CreationIdentity,
)
from app.modules.negotiation.exceptions import NegotiationStateConflictError
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.schemas import (
    NegotiationCreate,
    NegotiationFinalReviewRequest,
    NegotiationReceiverConfirm,
    NegotiationResponse,
    NegotiationReviewRequest,
)
from app.modules.negotiation.service import NegotiationService

log = logging.getLogger(__name__)
CREATE_KEY_CONSTRAINT = "uq_negotiation_create_key"


def creation_request_hash(payload: NegotiationCreate, group_id: UUID) -> str:
    """只摘要规范化请求，不读取会变化的博主、商品或组合价格。"""
    normalized = payload.model_dump(mode="json")
    normalized["account_ids"] = sorted(
        str(value) for value in set(payload.account_ids or [payload.blogger_id])
    )
    if payload.quote_amount is not None:
        normalized["quote_amount"] = format(payload.quote_amount, ".2f")
    # 这两个字段“省略”和“显式 null”会影响字段写权限/主商品推定，不能合并。
    normalized["explicit_fields"] = sorted(
        payload.model_fields_set & {"quote_amount", "goods_main_id"}
    )
    normalized["creation_group_id"] = str(group_id)
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def success_result(
    row_key: str, response: NegotiationResponse, *, reused: bool = False
) -> BulkItemResult:
    return BulkItemResult(
        row_key=row_key,
        negotiation_id=response.id,
        status="success",
        code="REUSED" if reused else "OK",
        message="已找到此前创建的谈款" if reused else "操作成功",
        reused=reused,
        negotiation=response,
    )


class BulkNegotiationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _user(self, user_id: UUID, tenant_id: UUID) -> User:
        # 回滚会使 ORM 实例过期，每项重新读取，避免复用上一项失效的 User。
        user = await self.session.scalar(
            select(User)
            .where(
                User.id == user_id,
                User.tenant_id == tenant_id,
            )
            .execution_options(populate_existing=True)
        )
        if user is None or user.status != "active":
            raise AccountDisabledError()
        return user

    async def _run(
        self,
        row_key: str,
        negotiation_id: UUID | None,
        user_id: UUID,
        tenant_id: UUID,
        call: Callable[[User], Awaitable[BulkItemResult]],
    ) -> BulkItemResult:
        try:
            result = await call(await self._user(user_id, tenant_id))
            # 原业务方法逐项 commit；lookup/replay 也结束本项读事务。
            await self.session.commit()
            return result
        except SchemaValidationError as exc:
            await self.session.rollback()
            reasons = [
                f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
                for error in exc.errors(
                    include_input=False, include_context=False, include_url=False
                )
            ]
            return BulkItemResult(
                row_key=row_key,
                negotiation_id=negotiation_id,
                status="failed",
                code="VALIDATION_ERROR",
                message="；".join(reasons),
            )
        except AppException as exc:
            await self.session.rollback()
            # 不回传 details/他人内容。原单看不到时也不进行额外探测。
            skipped = (
                exc.status_code in {401, 403, 404, 409} or exc.code == "ILLEGAL_STATE_TRANSITION"
            ) and exc.code != "CREATE_KEY_CONFLICT"
            return BulkItemResult(
                row_key=row_key,
                negotiation_id=negotiation_id,
                status="skipped" if skipped else "failed",
                code=exc.code,
                message=exc.message,
            )
        except Exception:
            await self.session.rollback()
            log.exception("negotiation_bulk_item_failed", extra={"row_key": row_key})
            return BulkItemResult(
                row_key=row_key,
                negotiation_id=negotiation_id,
                status="failed",
                code="INTERNAL_ERROR",
                message="本项处理异常，请核对已有单据后重试",
            )

    async def _existing(self, user: User, key: UUID) -> tuple[UUID, str] | None:
        row = (
            await self.session.execute(
                select(Negotiation.id, Negotiation.creation_request_hash).where(
                    Negotiation.tenant_id == user.tenant_id,
                    Negotiation.creation_pr_id == user.id,
                    Negotiation.client_create_key == key,
                )
            )
        ).one_or_none()
        return (row.id, row.creation_request_hash) if row is not None else None

    async def _replay(
        self, user: User, key: UUID, existing: tuple[UUID, str], digest: str
    ) -> BulkItemResult:
        response = await NegotiationService(self.session).get(existing[0], user)
        if existing[1] != digest:
            raise NegotiationStateConflictError(
                "该行已创建，但请求内容不同；请打开原谈款编辑，新合作请使用新行键",
                code="CREATE_KEY_CONFLICT",
            )
        return success_result(str(key), response, reused=True)

    async def _create(self, user: User, key: UUID, group_id: UUID, raw: dict) -> BulkItemResult:
        payload = NegotiationCreate.model_validate(raw)
        digest = creation_request_hash(payload, group_id)
        existing = await self._existing(user, key)
        if existing is not None:
            return await self._replay(user, key, existing, digest)
        user_id, tenant_id = user.id, user.tenant_id
        try:
            response = await NegotiationService(self.session).create(
                payload,
                user,
                creation_identity=CreationIdentity(key, group_id, digest),
            )
        except IntegrityError as exc:
            await self.session.rollback()
            if CREATE_KEY_CONSTRAINT not in str(exc):
                raise
            user = await self._user(user_id, tenant_id)
            existing = await self._existing(user, key)
            if existing is None:
                raise
            return await self._replay(user, key, existing, digest)
        return success_result(str(key), response)

    async def create(self, request: BulkCreateRequest, user: User) -> BulkResponse:
        user_id, tenant_id = user.id, user.tenant_id
        results = []
        for item in request.items:
            create_one = partial(
                self._create,
                key=item.client_create_key,
                group_id=request.creation_group_id,
                raw=item.payload,
            )
            results.append(
                await self._run(str(item.client_create_key), None, user_id, tenant_id, create_one)
            )
        return BulkResponse.from_results(results)

    async def lookup(self, request: BulkLookupRequest, user: User) -> BulkResponse:
        user_id, tenant_id = user.id, user.tenant_id
        results = []
        for key in request.client_create_keys:
            lookup_one = partial(self._lookup, key=key)
            results.append(await self._run(str(key), None, user_id, tenant_id, lookup_one))
        return BulkResponse.from_results(results)

    async def _lookup(self, actor: User, key: UUID) -> BulkItemResult:
        existing = await self._existing(actor, key)
        if existing is None:
            return BulkItemResult(
                row_key=str(key),
                status="skipped",
                code="NOT_CREATED",
                message="未找到当前账号此前创建的这一行",
            )
        return success_result(
            str(key), await NegotiationService(self.session).get(existing[0], actor), reused=True
        )

    async def _act(self, actor: User, action: BulkAction, item: BulkActionItem) -> BulkItemResult:
        service = NegotiationService(self.session)
        nid = item.negotiation_id
        if action == "submit":
            response = await service.submit(nid, actor)
        elif action == "review":
            response = await service.review(
                nid, NegotiationReviewRequest.model_validate(item.payload), actor
            )
        elif action == "final-review":
            response = await service.final_review(
                nid, NegotiationFinalReviewRequest.model_validate(item.payload), actor
            )
        else:
            response = await service.confirm_receiver(
                nid, NegotiationReceiverConfirm.model_validate(item.payload), actor
            )
        return success_result(str(nid), response)

    async def perform(
        self, action: BulkAction, request: BulkActionRequest, user: User
    ) -> BulkResponse:
        user_id, tenant_id = user.id, user.tenant_id
        seen: set[UUID] = set()
        results = []
        for item in request.items:
            nid = item.negotiation_id
            if nid in seen:
                results.append(
                    BulkItemResult(
                        row_key=str(nid),
                        negotiation_id=nid,
                        status="skipped",
                        code="DUPLICATE_ITEM",
                        message="本次请求已处理此单，重复项未执行",
                    )
                )
                continue
            seen.add(nid)
            act = partial(self._act, action=action, item=item)
            results.append(await self._run(str(nid), nid, user_id, tenant_id, act))
        return BulkResponse.from_results(results)
