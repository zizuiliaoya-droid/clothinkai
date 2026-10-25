"""R8 输入合同；SQL/真实租户与分页覆盖在集成用例中。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.promotion.schemas import PromotionListFilters


def test_filter_dimensions_are_preserved_together() -> None:
    goods, pr = uuid4(), uuid4()
    f = PromotionListFilters(
        goods_main_id=goods, seasons=["春季", "夏季"], unassigned_only=True, pr_id=pr
    )
    assert f.model_dump()["goods_main_id"] == goods
    assert f.model_dump()["seasons"] == ["春季", "夏季"]
    assert f.model_dump()["unassigned_only"] is True
    assert f.pr_id == pr  # 未分配不能覆盖具体 PR 的约束


@pytest.mark.parametrize("seasons", [["季"] * 21, ["季" * 65], [""]])
def test_seasons_are_bounded(seasons: list[str]) -> None:
    with pytest.raises(ValidationError):
        PromotionListFilters(seasons=seasons)


def test_empty_seasons_and_default_unassigned() -> None:
    assert PromotionListFilters(seasons=[]).model_dump()["seasons"] == []
    assert PromotionListFilters().model_dump()["unassigned_only"] is False
