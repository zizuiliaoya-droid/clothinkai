"""商品（goods_main）对外 schema。

1b 之后报表以商品为主体，但商品管理页（1c）还没做，所以这里先只暴露「按款式查商品」
这一个查询 —— 录推广时需要知道某个款式归属哪些商品（单品 / 套装）。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GoodsOption(BaseModel):
    """商品候选项：够前端做下拉选择与展示套装标记。"""

    model_config = ConfigDict(from_attributes=True)

    goods_main_id: UUID
    goods_code: str
    goods_title: str
    is_suit: bool = False


__all__ = ["GoodsOption"]
