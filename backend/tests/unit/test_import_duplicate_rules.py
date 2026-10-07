"""8a-6：导入重复规则的声明护栏（设计 §4.1，J1）。

- 每个已注册来源都有声明（以后加来源忘了声明会红）
- 五类写死来源的期望声明写死在这里：只改声明不改实现时这条红（实现侧由 AC 48 的回归测试守）
- 可切换来源（商品资料、博主）声明的策略在 adapter 的 ``supported_policies`` 范围内

注册表是类级 dict，现有集成测试会 ``clear()`` 后只塞一个 adapter，conftest 里还有
``fake_source``：照 ``test_summary_import_trigger.py`` 保存 → 清空 → 注册 → 放回。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.modules.importer.duplicate_rules import (
    DUPLICATE_RULES,
    SWITCHABLE_POLICIES,
    DuplicatePolicy,
    DuplicateRule,
    is_configurable,
    rule_for,
)
from app.modules.importer.registry import ImportAdapterRegistry

# 行为写死在 adapter 里的来源：声明只作文档与测试基准（结款单永不覆盖、遇已有直接拒绝，FB3）
_FIXED_EXPECTED: dict[str, DuplicatePolicy] = {
    "manual_settlement": DuplicatePolicy.REJECT,
    "manual_promotion": DuplicatePolicy.APPEND,
    "manual_tao_order": DuplicatePolicy.APPEND,
    "manual_brush_order": DuplicatePolicy.APPEND,
    "qianniu": DuplicatePolicy.OVERWRITE,
    "wanxiangtai": DuplicatePolicy.OVERWRITE,
    "huitun": DuplicatePolicy.OVERWRITE,
}
_CONFIGURABLE = ("manual_style_sku", "manual_blogger")


@pytest.fixture
def real_registry() -> Iterator[None]:
    from app.main import register_import_adapters

    saved = dict(ImportAdapterRegistry._adapters)
    ImportAdapterRegistry.clear()
    register_import_adapters()
    try:
        yield
    finally:
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry._adapters.update(saved)


@pytest.mark.unit
@pytest.mark.usefixtures("real_registry")
class TestDeclarations:
    def test_every_source_declared(self) -> None:
        assert set(DUPLICATE_RULES) == set(ImportAdapterRegistry.sources())

    def test_configurable_policies_supported(self) -> None:
        # 商品资料 adapter 在 8a-4 重写后才按 policy 分支，届时把 manual_style_sku 加进来
        for source in ("manual_blogger",):
            adapter = ImportAdapterRegistry.get(source)
            supported = getattr(adapter, "supported_policies", None)
            assert supported is not None, source
            assert rule_for(source).policy in supported, source
            assert frozenset(supported) == SWITCHABLE_POLICIES


@pytest.mark.unit
class TestFixedDeclarations:
    def test_fixed_source_declarations(self) -> None:
        for source, policy in _FIXED_EXPECTED.items():
            rule = rule_for(source)
            assert rule.policy is policy, source
            assert rule.configurable is False, source

    def test_configurable_sources(self) -> None:
        for source in _CONFIGURABLE:
            rule = rule_for(source)
            assert rule == DuplicateRule(rule.policy, key=rule.key, configurable=True)
            assert rule.policy is DuplicatePolicy.COMPARE
            assert rule.fill_empty is True
        assert {s for s in DUPLICATE_RULES if is_configurable(s)} == set(_CONFIGURABLE)

    def test_unknown_source_not_configurable(self) -> None:
        assert is_configurable("fake_source") is False
        with pytest.raises(KeyError):
            rule_for("fake_source")

    def test_rule_for_reads_dict_each_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """改声明即改行为：rule_for 每次现查 dict（AC 53 的前提）。"""
        replaced = DuplicateRule(DuplicatePolicy.OVERWRITE, key="x", configurable=True)
        monkeypatch.setitem(DUPLICATE_RULES, "manual_blogger", replaced)
        assert rule_for("manual_blogger") is replaced
