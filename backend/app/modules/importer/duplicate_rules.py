"""导入的重复规则：每个来源一处声明（8a-6，设计 §4.1，J1）。

- ``configurable=True`` 的两个来源（商品资料、博主）：adapter 每行调 ``rule_for(source)``
  按 ``policy`` 分支，改这里的声明即改行为（AC 53 在测试里把声明换成 OVERWRITE 验证）
- ``configurable=False`` 的来源（结款、推广单、千牛 / 万相台、拍单刷单、灰豚）：行为写死在各自
  adapter 里，声明只作文档与测试基准，改声明**不改**行为——财务结款「永不覆盖、遇已有直接拒绝」
  （FB3）不应是能被顺手切换的开关。两道护栏：``test_fixed_source_declarations``（只改声明会红）
  与 AC 48 的回归测试（只改实现会红）

同一份文件再传由文件哈希拦下（409，NF-2），与这里的规则无关。不做租户级配置界面。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DuplicatePolicy(StrEnum):
    """命中已有对象时怎么办。"""

    COMPARE = (
        "compare"  # 两边都有值且不同 → 冲突；系统值为空 → 补空（fill_empty 时）；相同 → 重复已跳过
    )
    OVERWRITE = "overwrite"  # 以文件为准覆盖（只写文件给了值的字段）
    KEEP = "keep"  # 已存在不动、不比较，计「重复已跳过」
    REJECT = "reject"  # 已存在 → 该行失败
    APPEND = "append"  # 没有判重键，每行新建


@dataclass(frozen=True)
class DuplicateRule:
    """一个来源的重复规则。"""

    policy: DuplicatePolicy
    key: str  # 判重键说明（文档与界面展示用）
    fill_empty: bool = True  # 补充二 Q1：系统值为空时直接补；False = 当冲突
    configurable: bool = False  # True：adapter 每行按 policy 分支；False：行为写死在 adapter 里


# 可切换来源支持的三种策略（两个 adapter 的 supported_policies 与此一致）
SWITCHABLE_POLICIES: frozenset[DuplicatePolicy] = frozenset(
    {DuplicatePolicy.COMPARE, DuplicatePolicy.OVERWRITE, DuplicatePolicy.KEEP}
)


DUPLICATE_RULES: dict[str, DuplicateRule] = {
    # 商品资料：款号；SKU 编码；商品见 §5.3。「商品名称」只在新建时写入、不比较
    "manual_style_sku": DuplicateRule(
        DuplicatePolicy.COMPARE, key="款号；SKU 编码；单品商品", configurable=True
    ),
    # 博主：小红书 ID（主键改造归 8b）
    "manual_blogger": DuplicateRule(DuplicatePolicy.COMPARE, key="小红书 ID", configurable=True),
    # 财务结款单：推广单一对一，已有结算单 → 该行失败，不进冲突（FB3，写死）
    "manual_settlement": DuplicateRule(DuplicatePolicy.REJECT, key="推广单"),
    # 推广单：每行新建，永不覆盖（FB3）
    "manual_promotion": DuplicateRule(DuplicatePolicy.APPEND, key="无"),
    # 拍单 / 刷单：只新增
    "manual_tao_order": DuplicateRule(DuplicatePolicy.APPEND, key="无"),
    "manual_brush_order": DuplicateRule(DuplicatePolicy.APPEND, key="无"),
    # 千牛 / 万相台日报：平台 ID + 日期，覆盖（以最新导出为准，A13）
    "qianniu": DuplicateRule(DuplicatePolicy.OVERWRITE, key="平台 ID + 日期"),
    "wanxiangtai": DuplicateRule(DuplicatePolicy.OVERWRITE, key="平台 ID + 日期"),
    # 灰豚博主画像：小红书 ID，覆盖画像（A13）
    "huitun": DuplicateRule(DuplicatePolicy.OVERWRITE, key="小红书 ID"),
}


def rule_for(source: str) -> DuplicateRule:
    """取来源的规则。每次调用现查 dict（测试可直接改 dict 项）；未声明的来源抛 KeyError。"""
    return DUPLICATE_RULES[source]


def is_configurable(source: str) -> bool:
    """来源的重复规则是否可切换（未声明的来源当不可切换，如测试里的 ``fake_source``）。"""
    rule = DUPLICATE_RULES.get(source)
    return rule is not None and rule.configurable


__all__ = [
    "DUPLICATE_RULES",
    "SWITCHABLE_POLICIES",
    "DuplicatePolicy",
    "DuplicateRule",
    "is_configurable",
    "rule_for",
]
