"""applier 的校验与留痕（设计 §4.5.1、§4.5.2）：博主（8a-6）、款式 / SKU / 商品（8a-4）。"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.modules.importer.compare import MONEY_REASON
from app.modules.importer.conflict_appliers import (
    BRAND_REASON,
    CONFLICT_APPLIERS,
    EXTERNAL_IMAGE_REASON,
    GENERIC_INVALID_REASON,
    SKU_SOURCING_VALUES,
    ApplierValueError,
    BloggerApplier,
    GoodsApplier,
    SkuApplier,
    StyleApplier,
    build_object_audit,
    invalid_reason,
)

_APPLIER = BloggerApplier()


class _FakeSession:
    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


def _blogger(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "nickname": "小美",
        "platform": "小红书",
        "wechat": None,
        "phone": None,
        "follower_count": 1000,
        "blogger_type": "腰部",
        "gender_target": None,
        "category_tags": ["美妆"],
        "quality_tags": [],
        "quote": Decimal("60.00"),
        "cooperation_history": None,
        "remark": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestBloggerCheck:
    def test_registered(self) -> None:
        assert isinstance(CONFLICT_APPLIERS["blogger"], BloggerApplier)
        assert CONFLICT_APPLIERS["blogger"].audit_action == "blogger.update"
        assert CONFLICT_APPLIERS["blogger"].audit_resource == "blogger"

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        [
            ("nickname", "  ", "不能为空"),
            ("nickname", "x" * 129, "超过 128 字"),
            ("platform", "x" * 17, "平台是判重键，不能经导入修改"),
            ("blogger_type", "x" * 17, "超过 16 字"),
            ("gender_target", "x" * 17, "超过 16 字"),
            ("wechat", "x" * 65, "超过 64 字"),
            ("phone", "1" * 33, "超过 32 字"),
            ("follower_count", -1, "必须为 0 到 2147483647 之间的整数"),
            ("follower_count", 2**31, "必须为 0 到 2147483647 之间的整数"),
            ("follower_count", "12", GENERIC_INVALID_REASON),
            ("quote", "-0.01", "必须为非负数字且小于 1 亿"),
            ("quote", "100000000", "必须为非负数字且小于 1 亿"),
            ("category_tags", [f"t{i}" for i in range(21)], "最多 20 项"),
            ("quality_tags", "美妆", "系统标签只读"),
            ("nickname", None, "不能为空"),
        ],
    )
    def test_invalid(self, field: str, value: Any, reason: str) -> None:
        with pytest.raises(ApplierValueError) as exc_info:
            _APPLIER.check(field, value)
        assert exc_info.value.field == field
        assert exc_info.value.reason == reason
        # 原因只用固定文案，不含值
        if isinstance(value, str) and len(value) > 3:
            assert value not in exc_info.value.reason

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("nickname", " 小美 ", "小美"),
            ("wechat", "x" * 64, "x" * 64),
            ("follower_count", 2**31 - 1, 2**31 - 1),
            ("follower_count", 0, 0),
            ("quote", "99999999.99", Decimal("99999999.99")),
            ("quote", "60", Decimal("60.00")),
            ("category_tags", ["美妆", " 护肤 "], ["美妆", "护肤"]),
            ("remark", "x" * 5000, "x" * 5000),
        ],
    )
    def test_valid(self, field: str, value: Any, expected: Any) -> None:
        assert _APPLIER.check(field, value) == expected

    def test_invalid_reason_never_uses_exception_text(self) -> None:
        class _M(BaseModel):
            cost: int

        with pytest.raises(ValidationError) as exc_info:
            _M(cost="60.00-secret")  # type: ignore[arg-type]
        assert invalid_reason(exc_info.value) == GENERIC_INVALID_REASON
        assert invalid_reason(ValueError("60.00")) == GENERIC_INVALID_REASON
        assert invalid_reason(InvalidOperation()) == GENERIC_INVALID_REASON
        assert invalid_reason(ApplierValueError("quote", "固定文案")) == "固定文案"


@pytest.mark.unit
class TestBloggerApply:
    async def test_follower_count_does_not_recompute_type(self) -> None:
        obj = _blogger(follower_count=1000, blogger_type="腰部")
        session = _FakeSession()
        changes = await _APPLIER.apply(session, obj, {"follower_count": 2_000_000})  # type: ignore[arg-type]
        assert changes == {"follower_count": (1000, 2_000_000)}
        assert obj.follower_count == 2_000_000
        assert obj.blogger_type == "腰部"
        assert session.flushes == 1

    async def test_same_value_not_written(self) -> None:
        obj = _blogger(quote=Decimal("60.00"), category_tags=["美妆", "护肤"])
        session = _FakeSession()
        changes = await _APPLIER.apply(
            session,  # type: ignore[arg-type]
            obj,
            {"quote": Decimal("60"), "category_tags": ["护肤", "美妆"]},
        )
        assert changes == {}
        assert session.flushes == 0

    def test_current_values_normalized(self) -> None:
        obj = _blogger(quote=Decimal("60"), nickname=" 小美 ", category_tags=[])
        values = _APPLIER.current_values(obj, ["quote", "nickname", "category_tags", "wechat"])
        assert values == {
            "quote": "60.00",
            "nickname": "小美",
            "category_tags": None,
            "wechat": None,
        }


@pytest.mark.unit
class TestObjectAudit:
    def test_sensitive_only_changed_flag(self) -> None:
        batch_id = uuid4()
        before, after = build_object_audit(
            _APPLIER,
            {
                "quote": (Decimal("60.00"), Decimal("65.00")),
                "wechat": (None, "wx"),
                "phone": ("1", "2"),
                "remark": (None, " 新备注 "),
                "follower_count": (1000, 2000),
                "category_tags": (["b"], ["b", "a"]),
            },
            via="import_fill",
            batch_id=batch_id,
            row_number=3,
        )
        assert before == {"remark": None, "follower_count": 1000, "category_tags": ["b"]}
        assert after["quote_changed"] is True
        assert after["wechat_changed"] is True
        assert after["phone_changed"] is True
        for name in ("quote", "wechat", "phone"):
            assert name not in after
            assert name not in before
        assert after["remark"] == " 新备注 "  # TEXT 不去空白
        assert after["category_tags"] == ["a", "b"]
        assert after["via"] == "import_fill"
        assert after["import_batch_id"] == str(batch_id)
        assert after["row_number"] == 3
        assert "import_conflict_id" not in after
        json.dumps(before)
        dumped = json.dumps(after, ensure_ascii=False)
        assert "65.00" not in dumped
        assert "wx" not in dumped

    def test_single_field_and_conflict_id(self) -> None:
        conflict_id = uuid4()
        before, after = build_object_audit(
            _APPLIER,
            {"quote": (Decimal("60.00"), Decimal("65.00"))},
            via="import_conflict",
            batch_id=None,
            conflict_id=conflict_id,
        )
        assert before == {}
        assert after == {
            "quote_changed": True,
            "via": "import_conflict",
            "import_batch_id": None,
            "import_conflict_id": str(conflict_id),
        }
        json.dumps(after)


# ---------------------------------------------------------------------------
# 款式 / SKU / 商品（8a-4）
# ---------------------------------------------------------------------------

_STYLE = StyleApplier()
_SKU = SkuApplier()
_GOODS = GoodsApplier()


def _sku(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "color": "红",
        "size": "M",
        "base_price": None,
        "cost_price": None,
        "purchase_price": Decimal("55.00"),
        "tag_price": None,
        "sourcing_type": "采购",
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestStyleSkuGoodsAppliers:
    def test_registered(self) -> None:
        assert isinstance(CONFLICT_APPLIERS["style"], StyleApplier)
        assert isinstance(CONFLICT_APPLIERS["sku"], SkuApplier)
        assert isinstance(CONFLICT_APPLIERS["goods"], GoodsApplier)
        assert (
            CONFLICT_APPLIERS["goods"].audit_action,
            CONFLICT_APPLIERS["goods"].audit_resource,
        ) == (
            "goods.update",
            "goods_main",
        )
        assert CONFLICT_APPLIERS["style"].audit_action == "style.update"
        assert CONFLICT_APPLIERS["sku"].audit_action == "sku.update"

    @pytest.mark.parametrize(
        ("applier", "field", "value", "reason"),
        [
            (_STYLE, "external_image_url", "javascript:alert(1)", EXTERNAL_IMAGE_REASON),
            (_STYLE, "external_image_url", "ftp://x.invalid/a.jpg", EXTERNAL_IMAGE_REASON),
            (_SKU, "color", "  ", "不能为空"),
            (_SKU, "color", "x" * 65, "超过 64 字"),
            (_SKU, "size", "x" * 33, "超过 32 字"),
            (_SKU, "base_price", "100000000", MONEY_REASON),
            (_SKU, "cost_price", "-1", MONEY_REASON),
            (_SKU, "tag_price", "abc", MONEY_REASON),
            (_SKU, "sourcing_type", "进口", "必须为 自产/外采/混合/采购/代发 之一"),
            (_GOODS, "short_name", "x" * 33, "超过 32 字"),
            (_GOODS, "season", "x" * 65, "超过 64 字"),
            (_GOODS, "brand_id", "not-a-uuid", BRAND_REASON),
        ],
    )
    def test_invalid(self, applier: Any, field: str, value: Any, reason: str) -> None:
        with pytest.raises(ApplierValueError) as exc_info:
            applier.check(field, value)
        assert (exc_info.value.field, exc_info.value.reason) == (field, reason)

    @pytest.mark.parametrize("sourcing", sorted(SKU_SOURCING_VALUES))
    def test_sourcing_values_accepted(self, sourcing: str) -> None:
        assert _SKU.check("sourcing_type", sourcing) == sourcing

    def test_money_quantized(self) -> None:
        assert _SKU.check("base_price", "60") == Decimal("60.00")
        assert _SKU.check("cost_price", "99999999.99") == Decimal("99999999.99")

    def test_current_values_purchase_sourcing_does_not_raise(self) -> None:
        """库里「采购 / 代发」读当前值不经 SourcingType(...)，不抛异常（N8）。"""
        for sourcing in ("采购", "代发"):
            values = _SKU.current_values(_sku(sourcing_type=sourcing), [s.name for s in _SKU.specs])
            assert values["sourcing_type"] == sourcing
            assert values["purchase_price"] == "55.00"

    async def test_self_produced_without_cost_can_write_base_price(self) -> None:
        """不调 validate_sku_sourcing_price：自产、没有成本价的 SKU 也能写基本售价（N8）。"""
        obj = _sku(sourcing_type="自产", cost_price=None)
        session = _FakeSession()
        changes = await _SKU.apply(
            session,  # type: ignore[arg-type]
            obj,
            {"base_price": _SKU.check("base_price", "60.00")},
        )
        assert changes == {"base_price": (None, Decimal("60.00"))}
        assert session.flushes == 1

    async def test_purchase_sourcing_overwrite_cost(self) -> None:
        obj = _sku(sourcing_type="采购", cost_price=Decimal("60.00"))
        changes = await _SKU.apply(
            _FakeSession(),  # type: ignore[arg-type]
            obj,
            {"cost_price": _SKU.check("cost_price", "65")},
        )
        assert changes == {"cost_price": (Decimal("60.00"), Decimal("65.00"))}

    def test_audit_json_safe_and_sensitive(self) -> None:
        """build_object_audit：基本售价 "60.00"、品牌 id 为字符串、成本价只记 _changed。"""
        brand_id = uuid4()
        before, after = build_object_audit(
            _SKU,
            {
                "base_price": (None, Decimal("60")),
                "cost_price": (Decimal("60.00"), Decimal("65.00")),
                "color": ("红", " 蓝 "),
            },
            via="import_overwrite",
            batch_id=uuid4(),
            row_number=2,
        )
        assert before == {"base_price": None, "color": "红"}
        assert after["base_price"] == "60.00"
        assert after["cost_price_changed"] is True
        assert "cost_price" not in after
        assert after["color"] == " 蓝 "  # TEXT 不去空白
        json.dumps(before)
        assert "65.00" not in json.dumps(after)

        gb, ga = build_object_audit(
            _GOODS,
            {"brand_id": (None, brand_id), "season": ("春", "夏")},
            via="import_conflict",
            batch_id=None,
            conflict_id=uuid4(),
        )
        assert ga["brand_id"] == str(brand_id)
        assert (gb["season"], ga["season"]) == ("春", "夏")
        json.dumps(gb)
        json.dumps(ga)

        sb, sa = build_object_audit(
            _STYLE,
            {"external_image_url": (None, "https://img.example.invalid/a.jpg")},
            via="import_fill",
            batch_id=None,
            row_number=1,
        )
        assert sb == {"external_image_url": None}
        assert sa["external_image_url"] == "https://img.example.invalid/a.jpg"

    def test_goods_current_brand_is_string(self) -> None:
        brand_id = uuid4()
        obj = SimpleNamespace(short_name=" 简 ", brand_id=brand_id, season=None)
        assert _GOODS.current_values(obj, ["short_name", "brand_id", "season"]) == {
            "short_name": "简",
            "brand_id": str(brand_id),
            "season": None,
        }
        assert _GOODS.check("brand_id", str(brand_id)) == brand_id
