"""7a-6 千牛 / 万相台 extra 列的聚合规则（纯函数，不连库）。

生产 09-17 店铺页「商品详情页跳出率」显示 6584.66：那天 171 个商品的跳出率被加在了一起。

表头与样本行都照仓库根两份真实模板逐字抄：

- ``千牛输入导入模版.xlsx``（生意参谋「商品_全部」导出）：表头在第 5 行，38 列；值是导出原样
  的文字（千分位、百分号、"-"）。样本取前 3 行。
- ``站内商品推广数据 表格导入.xlsx``（万相台站内推广导出）：表头在第 1 行，76 列；值是 openpyxl
  读出来的数字（空单元格是 None），日期写成 "YYYY-MM-DD"。样本取前 3 行。

样本行先过一遍导入 adapter 的 ``parse_row``：聚合的输入就是真实写进 extra 的样子。
集成测试 ``tests/integration/test_extra_aggregation.py`` 也从这里取样本。
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

import pytest

from app.modules.importer.adapters.qianniu import QianniuImportAdapter
from app.modules.importer.adapters.wanxiangtai import WanxiangtaiImportAdapter
from app.modules.report.extra_metrics import (
    EXTRA_COLUMN_KINDS,
    EXTRA_RATIO_FORMULAS,
    ExtraKind,
    aggregate_extra,
    parse_extra_number,
)

# 千牛输入导入模版.xlsx 第 5 行（38 列，原顺序）
QIANNIU_EXPORT_HEADERS: tuple[str, ...] = (
    "统计日期",
    "商品ID",
    "商品名称",
    "主商品ID",
    "商品类型",
    "货号",
    "商品状态",
    "商品标签",
    "商品访客数",
    "商品浏览量",
    "平均停留时长",
    "商品详情页跳出率",
    "商品收藏人数",
    "商品加购件数",
    "商品加购人数",
    "下单买家数",
    "下单件数",
    "下单金额",
    "下单转化率",
    "支付买家数",
    "支付件数",
    "支付金额",
    "商品支付转化率",
    "支付新买家数",
    "支付老买家数",
    "老买家支付金额",
    "聚划算支付金额",
    "访客平均价值",
    "成功退款金额",
    "竞争力评分",
    "年累计支付金额",
    "月累计支付金额",
    "月累计支付件数",
    "搜索引导支付转化率",
    "搜索引导访客数",
    "搜索引导支付买家数",
    "结构化详情引导转化率",
    "结构化详情引导成交占比",
)

# 站内商品推广数据 表格导入.xlsx 第 1 行（76 列，原顺序）
WANXIANGTAI_EXPORT_HEADERS: tuple[str, ...] = (
    "日期",
    "主体ID",
    "主体类型",
    "主体名称",
    "展现量",
    "点击量",
    "花费",
    "点击率",
    "平均点击花费",
    "千次展现花费",
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
    "点击转化率",
    "投入产出比",
    "含预售投产比",
    "总成交成本",
    "总购物车数",
    "直接购物车数",
    "间接购物车数",
    "加购率",
    "收藏宝贝数",
    "收藏店铺数",
    "店铺收藏成本",
    "总收藏加购数",
    "总收藏加购成本",
    "宝贝收藏加购数",
    "宝贝收藏加购成本",
    "总收藏数",
    "宝贝收藏成本",
    "宝贝收藏率",
    "加购成本",
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
    "引导访问潜客占比",
    "入会率",
    "入会量",
    "引导访问率",
    "深度访问量",
    "平均访问页面数",
    "成交新客数",
    "成交新客占比",
    "会员首购人数",
    "会员成交金额",
    "会员成交笔数",
    "成交人数",
    "人均成交笔数",
    "人均成交金额",
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
)

# 千牛模板第 6 ~ 8 行（表头下的前 3 行），按 QIANNIU_EXPORT_HEADERS 的顺序
QIANNIU_SAMPLE_ROWS: tuple[tuple[str, ...], ...] = (
    (
        "2026-06-13", "1017116707008",
        "LENNEA波点花边袖黑色短袖针织T恤女圆领修身打底衫夏季小众上衣",
        "1017116707008", "主商品", "2025317", "当前在线", "-",
        "11,269", "26,024", "6.92", "78.15%", "185", "754", "486", "76", "123", "17,191.00",
        "0.67%", "68", "109", "15,217.00", "0.60%", "61", "7", "1,525.00", "0.00", "1.35",
        "7,955.12", "-", "1,669,761.00", "280,941.00", "2,011", "3.61%", "582", "21", "-", "-",
    ),
    (
        "2026-06-13", "905381044899",
        "LENNEA法式小个子设计感减龄黄色连衣裙子女夏季无袖背心花苞短裙",
        "905381044899", "主商品", "2025945", "当前在线", "-",
        "12,118", "28,355", "4.81", "74.09%", "226", "462", "438", "71", "74", "13,211.00",
        "0.59%", "66", "69", "12,331.00", "0.54%", "63", "3", "545.00", "0.00", "1.02",
        "8,635.00", "-", "1,801,284.00", "241,800.00", "1,353", "1.45%", "1,174", "17", "-", "-",
    ),
    (
        "2026-06-13", "1023270397007",
        "LENNEA假两件波点短袖T恤女夏季甜酷风小飞袖荷叶边上衣撞色小衫",
        "1023270397007", "主商品", "2026003", "当前在线", "-",
        "3,579", "12,754", "5.62", "51.86%", "112", "529", "416", "55", "67", "9,400.00",
        "1.54%", "53", "63", "8,845.00", "1.48%", "51", "2", "274.00", "0.00", "2.47",
        "7,311.15", "-", "517,415.00", "190,560.00", "1,343", "2.72%", "478", "13", "-", "-",
    ),
)  # fmt: skip

# 万相台模板第 2 ~ 4 行，按 WANXIANGTAI_EXPORT_HEADERS 的顺序（日期原是 datetime，这里写成文字）
WANXIANGTAI_SAMPLE_ROWS: tuple[tuple[Any, ...], ...] = (
    (
        "2026-06-15", "1048410512272", "商品",
        "LENNEA蕾丝拼接花边短袖衬衫女夏季甜美减龄纯棉花苞袖方领上衣",
        598, 47, 6.46, 0.0786, 0.14, 10.8, 0, 0, 0, 0, 0, 0, 0, 373.8, 373.8, 3, 0, 3,
        0.06383, 57.86, 57.86, 2.15, 8, 5, 3, 0.17021, 6, 1, 6.46, 15, 0.43, 14, 0.46, 7,
        1.08, 0.12766, 0.81, 3, 441, 5, 1, 0, 0, 0, 0, 125, 43, 35, 0.81395, 0, 0, 0.20903,
        76, 3, 1, 1, 0, 0, 0, 1, 3, 373.8, 129.37, 686, 0, 0, 0, 0, 0, 0, 0, 0,
    ),
    (
        "2026-06-15", "1048406828843", "商品",
        "LENNEA湖蓝格子短袖衬衫女夏季日系设计感小众复古格纹娃娃领上衣",
        614, 38, 6.38, 0.06189, 0.17, 10.39, 0, 0, 0, 0, 0, 0, 0, 528, 528, 4, 0, 4,
        0.10526, 82.76, 82.76, 1.6, 12, 4, 8, 0.31579, 0, 0, None, 12, 0.53, 12, 0.53, 0,
        None, 0, 0.53, 4, 622.5, 0, 0, 0, 0, 0, 0, 174, 38, 31, 0.81579, 0, 0, 0.28339,
        131, 5, 3, 1, 0, 0, 0, 3, 1, 176, 178.92, 913, 0, 0, 0, 0, 0, 0, 0, 0,
    ),
    (
        "2026-06-15", "1048394656589", "商品",
        "LENNEA挂脖连衣裙女夏季法式辣妹无袖小个子短裙波点气质收腰裙子",
        799, 99, 17.26, 0.1239, 0.17, 21.6, 0, 0, 0, 0, 0, 0, 376, 634, 1010, 6, 2, 4,
        0.06061, 58.52, 58.52, 2.88, 31, 6, 25, 0.31313, 9, 4, 4.32, 44, 0.39, 40, 0.43, 13,
        1.92, 0.09091, 0.56, 6, 1193, 2, 7, 0, 0, 0, 2, 556, 90, 75, 0.83333, 0, 0, 0.69587,
        452, 6, 4, 1, 0, 0, 0, 4, 2, 252.5, 114.25, 2424, 0, 0, 0, 0, 0, 0, 0, 0,
    ),
)  # fmt: skip


def qianniu_row(index: int, **overrides: str) -> dict[str, Any]:
    """千牛样本第 index 行（从 0 起）→ {表头: 值}；overrides 按表头改值。"""
    row = dict(zip(QIANNIU_EXPORT_HEADERS, QIANNIU_SAMPLE_ROWS[index], strict=True))
    row.update(overrides)
    return row


def wanxiangtai_row(index: int, **overrides: Any) -> dict[str, Any]:
    """万相台样本第 index 行（从 0 起）→ {表头: 值}；overrides 按表头改值。"""
    row = dict(zip(WANXIANGTAI_EXPORT_HEADERS, WANXIANGTAI_SAMPLE_ROWS[index], strict=True))
    row.update(overrides)
    return row


def _qianniu_extra(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    adapter = QianniuImportAdapter()
    return [adapter.parse_row(row, None)["extra"] for row in rows]


def _wanxiangtai_extra(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    adapter = WanxiangtaiImportAdapter()
    return [adapter.parse_row(row, None)["extra"] for row in rows]


def _numbers(extra: dict[str, Any]) -> dict[str, Decimal]:
    parsed = {key: parse_extra_number(raw) for key, raw in extra.items()}
    return {key: value for key, value in parsed.items() if value is not None}


class TestParseExtraNumber:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("11,269", Decimal("11269")),
            ("78.15%", Decimal("78.15")),
            (" 6.92 ", Decimal("6.92")),
            ("1,669,761.00", Decimal("1669761.00")),
            ("0.00%", Decimal("0")),
            (0.0786, Decimal("0.0786")),
            (598, Decimal("598")),
        ],
    )
    def test_numbers(self, raw: Any, expected: Decimal) -> None:
        assert parse_extra_number(raw) == expected

    @pytest.mark.parametrize("raw", ["-", "", "   ", None, "abc", "当前在线", "NaN", "Infinity"])
    def test_not_numbers(self, raw: Any) -> None:
        assert parse_extra_number(raw) is None


class TestClassification:
    def test_header_constants_are_complete(self) -> None:
        assert len(QIANNIU_EXPORT_HEADERS) == len(set(QIANNIU_EXPORT_HEADERS)) == 38
        assert len(WANXIANGTAI_EXPORT_HEADERS) == len(set(WANXIANGTAI_EXPORT_HEADERS)) == 76

    def test_every_export_header_is_classified(self) -> None:
        missing = [
            header
            for header in (*QIANNIU_EXPORT_HEADERS, *WANXIANGTAI_EXPORT_HEADERS)
            if header not in EXTRA_COLUMN_KINDS
        ]
        assert missing == []

    def test_kind_counts(self) -> None:
        """与 plan 分类表逐类对数：千牛 8 / 18 / 3 / 9，万相台 4 / 51 / 0 / 21。"""
        qianniu = Counter(EXTRA_COLUMN_KINDS[h] for h in QIANNIU_EXPORT_HEADERS)
        wanxiangtai = Counter(EXTRA_COLUMN_KINDS[h] for h in WANXIANGTAI_EXPORT_HEADERS)
        assert qianniu == {
            ExtraKind.SKIP: 8,
            ExtraKind.ADDITIVE: 18,
            ExtraKind.CUMULATIVE: 3,
            ExtraKind.NON_ADDITIVE: 9,
        }
        assert wanxiangtai == {
            ExtraKind.SKIP: 4,
            ExtraKind.ADDITIVE: 51,
            ExtraKind.NON_ADDITIVE: 21,
        }

    @pytest.mark.parametrize(
        ("header", "kind"),
        [
            ("商品详情页跳出率", ExtraKind.NON_ADDITIVE),
            ("平均停留时长", ExtraKind.NON_ADDITIVE),
            ("竞争力评分", ExtraKind.NON_ADDITIVE),
            ("下单转化率", ExtraKind.NON_ADDITIVE),
            ("月累计支付金额", ExtraKind.CUMULATIVE),
            ("年累计支付金额", ExtraKind.CUMULATIVE),
            ("支付金额", ExtraKind.ADDITIVE),
            ("成功退款金额", ExtraKind.ADDITIVE),
            ("花费", ExtraKind.ADDITIVE),
            ("点击率", ExtraKind.NON_ADDITIVE),
            ("商品ID", ExtraKind.SKIP),
            ("主体名称", ExtraKind.SKIP),
            # 原来两处跳过清单里的历史表头
            ("日期", ExtraKind.SKIP),
            ("商品简称", ExtraKind.SKIP),
            ("商商品简称称", ExtraKind.SKIP),
        ],
    )
    def test_known_columns(self, header: str, kind: ExtraKind) -> None:
        assert EXTRA_COLUMN_KINDS[header] is kind

    def test_formula_operands_are_additive_columns_of_the_same_export(self) -> None:
        assert len(EXTRA_RATIO_FORMULAS) == 21
        for name, formula in EXTRA_RATIO_FORMULAS.items():
            assert EXTRA_COLUMN_KINDS[name] is ExtraKind.NON_ADDITIVE, name
            assert EXTRA_COLUMN_KINDS[formula.numerator] is ExtraKind.ADDITIVE, name
            assert EXTRA_COLUMN_KINDS[formula.denominator] is ExtraKind.ADDITIVE, name
            source = (
                QIANNIU_EXPORT_HEADERS
                if name in QIANNIU_EXPORT_HEADERS
                else WANXIANGTAI_EXPORT_HEADERS
            )
            assert {name, formula.numerator, formula.denominator} <= set(source), name

    @pytest.mark.parametrize(
        "header",
        [
            "平均访问页面数",
            "人均成交笔数",
            "入会率",
            "含预售投产比",
            "平均停留时长",
            "商品详情页跳出率",
            "竞争力评分",
            "结构化详情引导转化率",
            "结构化详情引导成交占比",
        ],
    )
    def test_unverified_ratios_are_not_recomputed(self, header: str) -> None:
        assert EXTRA_COLUMN_KINDS[header] is ExtraKind.NON_ADDITIVE
        assert header not in EXTRA_RATIO_FORMULAS

    def test_formulas_reproduce_sample_rows(self) -> None:
        """每个公式套到单行样本上，按位数舍入后与导出里的原值相等（全量 183 行另有脚本验过）。"""
        rows = [
            *_qianniu_extra(*(qianniu_row(i) for i in range(3))),
            *_wanxiangtai_extra(*(wanxiangtai_row(i) for i in range(3))),
        ]
        checked = 0
        for extra in rows:
            numbers = _numbers(extra)
            for name, formula in EXTRA_RATIO_FORMULAS.items():
                if name not in numbers or not numbers.get(formula.denominator):
                    continue
                assert formula.compute([numbers]) == numbers[name], (name, extra)
                checked += 1
        # 千牛 4 个公式 × 3 行 + 万相台 17 个 × 3 行，减去第 2 行分母为 0 的 2 个
        assert checked == 4 * 3 + 17 * 3 - 2


class TestAggregateQianniu:
    def test_same_day_two_products(self) -> None:
        result = aggregate_extra(_qianniu_extra(qianniu_row(0), qianniu_row(1)), same_day=True)
        # 比率 / 均值不相加：没有公式的给 None（原来是 78.15 + 74.09 = 152.24）
        assert result["商品详情页跳出率"] is None
        assert result["平均停留时长"] is None
        # 有公式的按 Σ分子 ÷ Σ分母 重算：(76 + 71) / (11269 + 12118) × 100
        assert result["下单转化率"] == "0.63"
        assert result["商品支付转化率"] == "0.57"  # (68 + 66) / 23387 × 100
        assert result["访客平均价值"] == "1.18"  # 27548 / 23387
        assert result["搜索引导支付转化率"] == "2.16"  # (21 + 17) / (582 + 1174) × 100
        # 计数与金额照旧相加
        assert result["商品访客数"] == "23387"
        assert result["支付金额"] == "27548.00"
        assert result["成功退款金额"] == "16590.12"
        # 同一天：累计列跨商品可以相加
        assert result["月累计支付金额"] == "522741.00"
        assert result["年累计支付金额"] == "3471045.00"
        assert result["月累计支付件数"] == "3364"
        # 全是 "-" 的列与 ID / 文本列不出现
        for header in ("竞争力评分", "结构化详情引导转化率", "结构化详情引导成交占比"):
            assert header not in result
        for header in ("统计日期", "商品ID", "商品名称", "主商品ID", "货号", "商品标签"):
            assert header not in result

    def test_cross_day_cumulative_is_not_summed(self) -> None:
        result = aggregate_extra(_qianniu_extra(qianniu_row(0), qianniu_row(1)), same_day=False)
        assert result["月累计支付金额"] is None
        assert result["年累计支付金额"] is None
        assert result["月累计支付件数"] is None
        # 其余列与同一天一样
        assert result["支付金额"] == "27548.00"
        assert result["下单转化率"] == "0.63"
        assert result["商品详情页跳出率"] is None

    def test_single_row_keeps_original(self) -> None:
        result = aggregate_extra(_qianniu_extra(qianniu_row(0)), same_day=False)
        assert result["商品详情页跳出率"] == "78.15"
        assert result["平均停留时长"] == "6.92"
        assert result["下单转化率"] == "0.67"
        assert result["月累计支付金额"] == "280941.00"
        assert result["商品访客数"] == "11269"
        assert "竞争力评分" not in result

    def test_zero_denominator_gives_none(self) -> None:
        """样本里有 2 个商品访客数为 0 的商品：比率导出为 "0.00%"，两行都是这样就算不出来。"""
        zero = {"商品访客数": "0", "下单买家数": "0", "下单转化率": "0.00%"}
        result = aggregate_extra(
            _qianniu_extra(qianniu_row(0, **zero), qianniu_row(1, **zero)), same_day=True
        )
        assert result["下单转化率"] is None
        assert result["商品访客数"] == "0"


class TestAggregateWanxiangtai:
    def test_two_days_recompute_with_precision(self) -> None:
        result = aggregate_extra(
            _wanxiangtai_extra(wanxiangtai_row(0), wanxiangtai_row(1)), same_day=False
        )
        assert result["点击率"] == "0.07013"  # (47 + 38) / (598 + 614)，5 位
        assert result["点击转化率"] == "0.08235"  # (3 + 4) / 85
        assert result["千次展现花费"] == "10.59"  # 12.84 / 1212 × 1000
        assert result["投入产出比"] == "70.23"  # 901.8 / 12.84
        assert result["平均点击花费"] == "0.15"  # 12.84 / 85
        assert result["花费"] == "12.84"
        assert result["展现量"] == "1212"
        # 样本验不了的 4 列多行给 None
        for header in ("含预售投产比", "平均访问页面数", "人均成交笔数", "入会率"):
            assert result[header] is None, header
        for header in ("日期", "主体ID", "主体类型", "主体名称"):
            assert header not in result

    def test_blank_ratio_cell_still_counts_as_a_row(self) -> None:
        """第 2 行收藏店铺数为 0，导出里「店铺收藏成本」是空的，花费却照算。

        两天的组要按 Σ花费 ÷ Σ收藏店铺数 重算，不能把第 1 天的 6.46 当成两天的值。
        """
        result = aggregate_extra(
            _wanxiangtai_extra(wanxiangtai_row(0), wanxiangtai_row(1)), same_day=False
        )
        assert result["店铺收藏成本"] == "12.84"  # (6.46 + 6.38) / (1 + 0)
        assert result["宝贝收藏成本"] == "2.14"  # 12.84 / (6 + 0)

    def test_single_row_keeps_original(self) -> None:
        result = aggregate_extra(_wanxiangtai_extra(wanxiangtai_row(0)), same_day=False)
        assert result["点击率"] == "0.0786"
        assert result["含预售投产比"] == "57.86"


class TestAggregateEdges:
    def test_unknown_column_is_summed_as_before(self) -> None:
        assert aggregate_extra(
            [{"租户自定义列": "1"}, {"租户自定义列": "2.5"}], same_day=False
        ) == {"租户自定义列": "3.5"}

    def test_rows_that_are_not_mappings_are_ignored(self) -> None:
        result = aggregate_extra([None, {"支付金额": "1,000.00"}], same_day=False)
        assert result == {"支付金额": "1000.00"}

    def test_empty_input(self) -> None:
        assert aggregate_extra([], same_day=True) == {}
