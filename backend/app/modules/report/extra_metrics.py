"""千牛 / 万相台 extra 列的聚合规则（店铺数据、投产报表与导出共用，只在这里维护）。

导入时千牛（生意参谋「商品_全部」）与万相台（站内推广）导出的整行原样存进 extra，
报表按日 / 按周 / 按商品聚合时再把这几十列汇总成一行。原来每一列都按数值相加，比率、
均值、累计、评分也被加起来：生产 09-17 店铺页「商品详情页跳出率」显示 6584.66 ——
那天 171 个商品的跳出率加在一起。

分类依据：表头逐字取自仓库根两份模板（``千牛输入导入模版.xlsx`` 第 5 行 38 列、
``站内商品推广数据 表格导入.xlsx`` 第 1 行 76 列），逐列归为四类：

- SKIP：ID / 文本 / 日期，不进报表（与原来两处的跳过清单取并集，另含历史表头
  「商品简称」「商商品简称称」）。
- ADDITIVE：计数与金额，多行相加。**没列出的键也按这一类**，维持原来的行为 ——
  租户导出里多出来的列不会因为这次改动变样。
- CUMULATIVE：年 / 月累计。每天的值已经包含之前的天：同一天跨商品可以相加，跨天相加
  就重复计了，按 NON_ADDITIVE 处理（调用方用 ``same_day`` 说明这一组是不是同一天）。
- NON_ADDITIVE：比率、均值、评分，多行不能相加。有公式的按 Σ分子 ÷ Σ分母 重算，
  没有公式的给 None（页面显示「—」、导出为空）。

「一行」指带着这一列的行，值是 "-" 或空也算：万相台在分母为 0 时把比率留空（收藏店铺数
为 0 那天「店铺收藏成本」是空的，花费却照算），只数有值的行的话，两天的组会被当成单行、
直接拿那一天的值冒充两天的值。只有一行带这一列时照原值（规范化后的写法，与改动前单行
时一样）。

只重算用真实导出验证过的公式：两份样本（千牛 140 行、万相台 43 行）逐行验算，下面 21 个
公式按各自的倍数与位数四舍五入后，1123 个行次里 1122 个与导出原值逐位相等，剩 1 个差末位 1
（访客平均价值未舍入是 2.364990，正卡在舍入边界上，导出写的 2.37）—— 都在显示精度内。
千牛的比率是百分数（"0.67%"，解析只去掉 %），所以 ×100；万相台的比率是小数，保留 5 位，
金额类保留 2 位。
「平均访问页面数」「人均成交笔数」样本里是取整后的值，验不出位数与舍入；「入会率」
「含预售投产比」样本里没有可验的非零值 —— 这 4 列不重算，多行给 None。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class ExtraKind(StrEnum):
    SKIP = "skip"
    ADDITIVE = "additive"
    CUMULATIVE = "cumulative"
    NON_ADDITIVE = "non_additive"


@dataclass(frozen=True)
class RatioFormula:
    """多行时的重算公式：Σ分子 ÷ Σ分母 × scale，ROUND_HALF_UP 到 places 位。"""

    numerator: str
    denominator: str
    scale: Decimal = Decimal(1)
    places: int = 2

    def compute(self, rows: Iterable[Mapping[str, Decimal]]) -> Decimal | None:
        """``rows`` 是已解析成数值的行。只用分子、分母都有数的行配对。

        没有配对的行或 Σ分母 = 0 → None（算不出来就不编一个数）。
        """
        pairs = [
            (row[self.numerator], row[self.denominator])
            for row in rows
            if self.numerator in row and self.denominator in row
        ]
        total_den = sum((den for _, den in pairs), Decimal(0))
        if not pairs or total_den == 0:
            return None
        total_num = sum((num for num, _ in pairs), Decimal(0))
        value = total_num / total_den * self.scale
        return value.quantize(Decimal(1).scaleb(-self.places), rounding=ROUND_HALF_UP)


# 千牛：生意参谋「商品_全部」导出（千牛输入导入模版.xlsx 第 5 行，38 列）
_QIANNIU_COLUMNS: dict[ExtraKind, tuple[str, ...]] = {
    ExtraKind.SKIP: (
        "统计日期",
        "商品ID",
        "商品名称",
        "主商品ID",
        "商品类型",
        "货号",
        "商品状态",
        "商品标签",
    ),
    ExtraKind.ADDITIVE: (
        "商品访客数",
        "商品浏览量",
        "商品收藏人数",
        "商品加购件数",
        "商品加购人数",
        "下单买家数",
        "下单件数",
        "下单金额",
        "支付买家数",
        "支付件数",
        "支付金额",
        "支付新买家数",
        "支付老买家数",
        "老买家支付金额",
        "聚划算支付金额",
        "成功退款金额",
        "搜索引导访客数",
        "搜索引导支付买家数",
    ),
    ExtraKind.CUMULATIVE: (
        "年累计支付金额",
        "月累计支付金额",
        "月累计支付件数",
    ),
    ExtraKind.NON_ADDITIVE: (
        # 有公式（见 EXTRA_RATIO_FORMULAS）
        "下单转化率",
        "商品支付转化率",
        "访客平均价值",
        "搜索引导支付转化率",
        # 没有公式：导出里没有能还原它们的分子分母
        "平均停留时长",
        "商品详情页跳出率",
        "竞争力评分",
        "结构化详情引导转化率",
        "结构化详情引导成交占比",
    ),
}

# 万相台：站内推广导出（站内商品推广数据 表格导入.xlsx 第 1 行，76 列）
_WANXIANGTAI_COLUMNS: dict[ExtraKind, tuple[str, ...]] = {
    ExtraKind.SKIP: (
        "日期",
        "主体ID",
        "主体类型",
        "主体名称",
    ),
    ExtraKind.ADDITIVE: (
        "展现量",
        "点击量",
        "花费",
        "总预售成交金额",
        "总预售成交笔数",
        "直接预售成交金额",
        "直接预售成交笔数",
        "间接预售成交金额",
        "间接预售成交笔数",
        "直接成交金额",
        "间接成交金额",
        "总成交金额",
        "总成交笔数",
        "直接成交笔数",
        "间接成交笔数",
        "总购物车数",
        "直接购物车数",
        "间接购物车数",
        "收藏宝贝数",
        "收藏店铺数",
        "总收藏加购数",
        "宝贝收藏加购数",
        "总收藏数",
        "拍下订单笔数",
        "拍下订单金额",
        "直接收藏宝贝数",
        "间接收藏宝贝数",
        "优惠券领取量",
        "购物金充值笔数",
        "购物金充值金额",
        "旺旺咨询量",
        "引导访问量",
        "引导访问人数",
        "引导访问潜客数",
        "入会量",
        "深度访问量",
        "成交新客数",
        "会员首购人数",
        "会员成交金额",
        "会员成交笔数",
        "成交人数",
        "自然流量转化金额",
        "自然流量曝光量",
        "宝贝优惠券抵扣金额",
        "宝贝优惠券撬动总成交",
        "宝贝优惠券撬动直接成交",
        "宝贝优惠券撬动点击",
        "平台补贴金额",
        "补贴引导成交金额",
        "发券补贴商品个数",
        "补贴引导成交人数",
    ),
    ExtraKind.CUMULATIVE: (),
    ExtraKind.NON_ADDITIVE: (
        # 有公式（见 EXTRA_RATIO_FORMULAS）
        "点击率",
        "平均点击花费",
        "千次展现花费",
        "点击转化率",
        "投入产出比",
        "总成交成本",
        "加购率",
        "加购成本",
        "店铺收藏成本",
        "总收藏加购成本",
        "宝贝收藏加购成本",
        "宝贝收藏成本",
        "宝贝收藏率",
        "引导访问潜客占比",
        "引导访问率",
        "成交新客占比",
        "人均成交金额",
        # 没有公式：样本验不了（见模块说明）
        "含预售投产比",
        "平均访问页面数",
        "人均成交笔数",
        "入会率",
    ),
}

# 历史文件里出现过、原来就跳过的表头（「日期」已在万相台里）
_LEGACY_SKIP = ("商品简称", "商商品简称称")

EXTRA_COLUMN_KINDS: dict[str, ExtraKind] = {
    header: kind
    for columns in (_QIANNIU_COLUMNS, _WANXIANGTAI_COLUMNS)
    for kind, headers in columns.items()
    for header in headers
} | dict.fromkeys(_LEGACY_SKIP, ExtraKind.SKIP)

_PERCENT = Decimal(100)

EXTRA_RATIO_FORMULAS: dict[str, RatioFormula] = {
    # 千牛：百分数，×100 保留 2 位
    "下单转化率": RatioFormula("下单买家数", "商品访客数", _PERCENT),
    "商品支付转化率": RatioFormula("支付买家数", "商品访客数", _PERCENT),
    "访客平均价值": RatioFormula("支付金额", "商品访客数"),
    "搜索引导支付转化率": RatioFormula("搜索引导支付买家数", "搜索引导访客数", _PERCENT),
    # 万相台：比率保留 5 位，金额保留 2 位
    "点击率": RatioFormula("点击量", "展现量", places=5),
    "平均点击花费": RatioFormula("花费", "点击量"),
    "千次展现花费": RatioFormula("花费", "展现量", Decimal(1000)),
    "点击转化率": RatioFormula("总成交笔数", "点击量", places=5),
    "投入产出比": RatioFormula("总成交金额", "花费"),
    "总成交成本": RatioFormula("花费", "总成交笔数"),
    "加购率": RatioFormula("总购物车数", "点击量", places=5),
    "加购成本": RatioFormula("花费", "总购物车数"),
    "店铺收藏成本": RatioFormula("花费", "收藏店铺数"),
    "总收藏加购成本": RatioFormula("花费", "总收藏加购数"),
    "宝贝收藏加购成本": RatioFormula("花费", "宝贝收藏加购数"),
    "宝贝收藏成本": RatioFormula("花费", "收藏宝贝数"),
    "宝贝收藏率": RatioFormula("收藏宝贝数", "点击量", places=5),
    "引导访问潜客占比": RatioFormula("引导访问潜客数", "引导访问人数", places=5),
    "引导访问率": RatioFormula("引导访问量", "展现量", places=5),
    "成交新客占比": RatioFormula("成交新客数", "成交人数", places=5),
    "人均成交金额": RatioFormula("总成交金额", "成交人数"),
}


def parse_extra_number(raw: Any) -> Decimal | None:
    """extra 里的一个值 → 数值。去千分位、去 %、去空白（"78.15%" → 78.15，不除以 100）。

    ""、"-"、None、认不出的文字与 NaN / 无穷 → None。
    """
    if raw is None:
        return None
    s = str(raw).replace(",", "").replace("%", "").strip()
    if s in ("", "-"):
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _kind(key: str) -> ExtraKind:
    return EXTRA_COLUMN_KINDS.get(key, ExtraKind.ADDITIVE)


def aggregate_extra(
    rows: Iterable[Mapping[str, Any] | None], *, same_day: bool
) -> dict[str, str | None]:
    """把一组 extra（同一天的多个商品，或同一商品的多天）聚合成一行。

    ``same_day``：这一组是不是同一天（决定累计列能不能相加）。结果的值是十进制字符串
    （``format(v, "f")``），多行时算不出来的是 None；一个数都没有的键不出现。
    """
    present: dict[str, int] = {}
    numbers: dict[str, list[Decimal]] = {}
    parsed_rows: list[dict[str, Decimal]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        parsed: dict[str, Decimal] = {}
        for key, raw in row.items():
            if _kind(key) is ExtraKind.SKIP:
                continue
            present[key] = present.get(key, 0) + 1
            value = parse_extra_number(raw)
            if value is None:
                continue
            parsed[key] = value
            numbers.setdefault(key, []).append(value)
        parsed_rows.append(parsed)

    result: dict[str, str | None] = {}
    for key, values in numbers.items():
        kind = _kind(key)
        if kind is ExtraKind.ADDITIVE or (kind is ExtraKind.CUMULATIVE and same_day):
            result[key] = format(sum(values, Decimal(0)), "f")
        elif present[key] == 1:
            result[key] = format(values[0], "f")
        else:
            formula = EXTRA_RATIO_FORMULAS.get(key)
            recomputed = formula.compute(parsed_rows) if formula is not None else None
            result[key] = None if recomputed is None else format(recomputed, "f")
    return result


__all__ = [
    "EXTRA_COLUMN_KINDS",
    "EXTRA_RATIO_FORMULAS",
    "ExtraKind",
    "RatioFormula",
    "aggregate_extra",
    "parse_extra_number",
]
