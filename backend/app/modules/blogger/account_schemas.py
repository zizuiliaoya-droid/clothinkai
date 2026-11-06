"""具体账号组合；价格快照仅由服务端生成，旧单可为 NULL。"""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AccountIds = Annotated[list[UUID], Field(min_length=1, max_length=10)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


class AccountIdentity(BaseModel):
    account_id: UUID
    platform: str
    account: str
    nickname: str


class AccountSnapshot(BaseModel):
    version: Literal[1] = 1
    primary_account_id: UUID
    group_id: UUID | None = None
    accounts: list[AccountIdentity] = Field(min_length=1, max_length=10)
    quote_id: UUID | None = None
    reference_amount: Decimal | None = None
    price_source: Literal["group_quote", "blogger_quote", "manual", "unknown", "exchange"]


class AccountGroupInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    account_ids: AccountIds


class AccountQuoteInput(BaseModel):
    account_ids: AccountIds
    amount: Amount


class AccountQuoteResponse(BaseModel):
    id: UUID
    account_ids: list[UUID]
    amount: Decimal | None


class AccountGroupResponse(BaseModel):
    id: UUID
    name: str
    account_ids: list[UUID]
    quotes: list[AccountQuoteResponse]


class AccountOption(BaseModel):
    id: UUID
    platform: str
    account: str
    nickname: str
    available_for_new_cooperation: bool


class AccountGroupView(BaseModel):
    group: AccountGroupResponse | None
    accounts: list[AccountOption]
    can_manage: bool
    can_read_quote: bool
    can_write_quote: bool
    can_write_amount: bool


class AccountQuoteResolve(BaseModel):
    blogger_id: UUID
    account_ids: AccountIds | None = None


class AccountQuotePreview(BaseModel):
    account_snapshot: AccountSnapshot
    reference_amount: Decimal | None
    matched: bool
    can_write_amount: bool
    can_read_quote: bool


class FulfillmentSummary(BaseModel):
    total: int
    completed: int
    missing_accounts: list[AccountIdentity]
    complete: bool
