"""批量仅编排单笔；行内载荷由单笔 schema 逐项校验，避免一项错误阻止全部。"""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.negotiation.schemas import NegotiationResponse

BulkAction = Literal["submit", "review", "final-review", "confirm-receiver"]


@dataclass(frozen=True)
class CreationIdentity:
    """服务端内部创建元数据，不是单笔接口的可写字段。"""

    client_create_key: UUID
    creation_group_id: UUID
    request_hash: str


class BulkCreateItem(BaseModel):
    client_create_key: UUID
    payload: dict[str, Any]


class BulkCreateRequest(BaseModel):
    creation_group_id: UUID
    items: list[BulkCreateItem] = Field(min_length=1, max_length=50)


class BulkLookupRequest(BaseModel):
    client_create_keys: list[UUID] = Field(min_length=1, max_length=50)


class BulkActionItem(BaseModel):
    negotiation_id: UUID
    payload: dict[str, Any] = Field(default_factory=dict)


class BulkActionRequest(BaseModel):
    items: list[BulkActionItem] = Field(min_length=1, max_length=50)


class BulkItemResult(BaseModel):
    row_key: str
    negotiation_id: UUID | None = None
    status: Literal["success", "skipped", "failed"]
    code: str
    message: str
    reused: bool = False
    negotiation: NegotiationResponse | None = None


class BulkResponse(BaseModel):
    results: list[BulkItemResult]
    success: int
    skipped: int
    failed: int

    @classmethod
    def from_results(cls, results: list[BulkItemResult]) -> "BulkResponse":
        return cls(
            results=results,
            success=sum(row.status == "success" for row in results),
            skipped=sum(row.status == "skipped" for row in results),
            failed=sum(row.status == "failed" for row in results),
        )
