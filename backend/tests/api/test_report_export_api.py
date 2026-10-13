"""BI / 报表导出 API 契约测试。"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook

from app.core.security.field_permissions import FieldPermissionContext
from app.core.security.permissions import EffectivePermissions
from app.modules.report.advanced_schemas import (
    ProductionReport,
    ProductionRow,
    Recent7Range,
    StoreReport,
    StoreReportRow,
    WorkProgressMetrics,
)
from app.modules.report.pr_scope import PR_SCOPE_ALL
from app.modules.report.production_columns import PRODUCTION_COLUMNS
from app.modules.report.store_columns import STORE_COLUMNS
from app.modules.report.work_progress_columns import (
    GOODS_HEADERS,
    PR_HEADERS,
    WEEK_HEADERS,
    WORK_PROGRESS_METRIC_COLUMNS,
)

_SUPER_CTX = FieldPermissionContext(frozenset(), frozenset(), frozenset(), is_superuser=True)
_ALL_PERMS = EffectivePermissions(user_id="test", scopes=frozenset({"*"}))


@pytest.mark.api
@pytest.mark.asyncio
class TestReportExportApiContract:
    async def test_bi_dashboard_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/reports/bi")
        assert resp.status_code == 401

    async def test_export_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/reports/production/export")
        assert resp.status_code == 401

    async def test_openapi_exposes_bi_and_export(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/reports/bi" in paths
        assert "/api/reports/bi/layout" in paths
        assert "/api/reports/{report_type}/export" in paths
        # 7e R6：BI 响应只有 store / goods 两块，遗留的 cards / charts 删了
        bi = resp.json()["components"]["schemas"]["BiDashboard"]["properties"]
        assert set(bi) == {
            "date_from",
            "date_to",
            "granularity",
            "exclude_brushing",
            "store",
            "goods",
        }

    async def test_export_rows_are_complete_and_numeric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.report import export_service as module

        dangerous_header = '=HYPERLINK("https://invalid.example","x")'
        dangerous_text = "  +SUM(1,1)"
        dangerous_short = "=1+1"
        goods_id = uuid4()
        production_row = ProductionRow(
            goods_id=goods_id,
            goods_code="S001",
            goods_short_name=dangerous_short,
            goods_title=dangerous_text,
            style_codes=["S001", "S002"],
            channel="普通",
            link_count=2,
            pay_amount=Decimal("100"),
            refund_amount=Decimal("10"),
            return_rate=Decimal("0.1"),
            confirmed_amount=Decimal("90"),
            promo_cost=Decimal("20"),
            ad_spend=Decimal("30"),
            total_spend=Decimal("50"),
            pay_orders=4,
            add_cart_count=5,
            add_cart_cost=Decimal("10"),
            ad_add_cart_count=3,
            ad_add_cart_cost=Decimal("10"),
            offsite_add_cart=2,
            pre_return_roi=Decimal("2"),
            net_roi=Decimal("1.8"),
            unit_deal_cost=Decimal("12.5"),
            recent7_pay_amount=Decimal("70"),
            recent7_total_spend=Decimal("7"),
            recent7_net_roi=Decimal("10"),
            goods_cost_total=Decimal("40"),
            gross_profit=Decimal("50"),
            gross_margin_rate=Decimal("0.5556"),
            extra={dangerous_header: "3"},
        )
        # 同一商品的直播行：extra 只挂在普通行（S1 偏差），直播行这一列留空
        live_row = production_row.model_copy(
            update={"channel": "直播", "link_count": 1, "offsite_add_cart": None, "extra": {}}
        )
        production_report = ProductionReport(
            items=[production_row, live_row],
            recent7_range=Recent7Range(date_from=date(2026, 7, 27), date_to=date(2026, 8, 2)),
        )
        production_calls: list[dict[str, object]] = []
        # service 给什么行导出就写什么行：分桶与 extra 聚合都在 StoreDailyService 里。
        # 两行故意落在同一周 —— 导出要是自己再按周合并一次，这里会变成一行。
        store_rows = [
            SimpleNamespace(
                date=date(2026, 8, 3),
                visitors=10,
                pay_amount=Decimal("20"),
                pay_orders=2,
                ad_spend_total=Decimal("3"),
                zhitongche_spend=Decimal("1"),
                yinli_spend=None,
                # 多行时算不出来的比率是 None → 导出为空单元格
                extra={"成交人数": "2", "下单转化率": None},
            ),
            SimpleNamespace(
                date=date(2026, 8, 4),
                visitors=5,
                pay_amount=Decimal("8"),
                pay_orders=1,
                ad_spend_total=None,
                zhitongche_spend=Decimal("2"),
                yinli_spend=Decimal("4"),
                extra={"成交人数": "1", "下单转化率": "0.63"},
            ),
        ]
        store_calls: list[dict[str, object]] = []

        class FakeProductionService:
            def __init__(self, _session: object) -> None:
                pass

            async def get_report(self, *_args: object, **kwargs: object) -> object:
                production_calls.append(kwargs)
                return production_report

        class FakeStoreDailyService:
            def __init__(self, _session: object) -> None:
                pass

            async def get_dashboard(self, *_args: object, **kwargs: object) -> list[object]:
                store_calls.append(kwargs)
                return store_rows

        monkeypatch.setattr(module, "ProductionService", FakeProductionService)
        monkeypatch.setattr(module, "StoreDailyService", FakeStoreDailyService)
        service = module.ReportExportService(None)  # type: ignore[arg-type]
        period = (date(2026, 8, 3), date(2026, 8, 4))

        production_headers, production_rows = await service._fetch_rows(
            uuid4(),
            "production",
            period,
            exclude_brushing=True,
            seasons=None,
            granularity="day",
            field_ctx=_SUPER_CTX,
        )
        # 表头逐列来自共用列定义（列顺序 = 页面），近 7 天三列带区间；extra 在最后
        assert production_headers == [
            "商品编码",
            "商品简称",
            "商品名称",
            "渠道",
            "千牛链接数",
            "含款号",
            "支付金额",
            "支付件数（不扣刷单）",
            "退款金额",
            "退货退款率",
            "实际销售金额",
            "站外推广费",
            "站内投放",
            "推广总花费",
            "退货前投产比",
            "净投产比",
            "推广单件成交成本",
            "总加购数",
            "总加购成本",
            "站内加购",
            "站内加购成本",
            "站外加购",
            "近 7 天（07-27 ~ 08-02）支付金额",
            "近 7 天（07-27 ~ 08-02）推广总花费",
            "近 7 天（07-27 ~ 08-02）净投产比",
            "销售货品总成本",
            "单品毛利",
            "单品利润率",
            dangerous_header,
        ]
        assert production_headers[:-1] == [
            module.export_header(c, production_report.recent7_range) for c in PRODUCTION_COLUMNS
        ]
        assert production_rows[0][:-1] == [
            "S001",
            dangerous_short,
            dangerous_text,
            "普通",
            2,
            "S001,S002",
            Decimal("100"),
            4,
            Decimal("10"),
            Decimal("0.1"),
            Decimal("90"),
            Decimal("20"),
            Decimal("30"),
            Decimal("50"),
            Decimal("2"),
            Decimal("1.8"),
            Decimal("12.5"),
            5,
            Decimal("10"),
            3,
            Decimal("10"),
            2,
            Decimal("70"),
            Decimal("7"),
            Decimal("10"),
            Decimal("40"),
            Decimal("50"),
            Decimal("0.5556"),
        ]
        # 同商品两渠道 → 两行；直播行站外加购 None、extra 空
        assert [r[3] for r in production_rows] == ["普通", "直播"]
        live = dict(zip(production_headers, production_rows[1], strict=True))
        assert live["站外加购"] is None and live[dangerous_header] is None
        assert len(production_headers) == len(production_rows[0]) == len(PRODUCTION_COLUMNS) + 1
        assert isinstance(module._cell(production_rows[0][6]), float)
        assert isinstance(module._cell(production_rows[0][-1]), float)
        # 超管 ctx → 毛利可见，原样交给 service；运营 ctx（能看成本价）→ 不给毛利
        assert production_calls[-1]["show_margin"] is True
        ops_ctx = FieldPermissionContext(frozenset({"operations"}), frozenset(), frozenset())
        await service._fetch_rows(
            uuid4(),
            "production",
            period,
            exclude_brushing=True,
            seasons=None,
            granularity="day",
            field_ctx=ops_ctx,
        )
        assert production_calls[-1]["show_margin"] is False
        assert module._cell(dangerous_header) == f"'{dangerous_header}"
        assert module._cell(dangerous_text) == f"'{dangerous_text}"
        assert module._cell(dangerous_short) == f"'{dangerous_short}"

        store_headers, grouped_store_rows = await service._fetch_rows(
            uuid4(),
            "store-daily",
            period,
            exclude_brushing=True,
            seasons=None,
            granularity="week",
            field_ctx=_SUPER_CTX,
        )
        assert store_headers[:7] == [
            "日期",
            "访客数",
            "支付额",
            "支付订单",
            "广告花费",
            "直通车花费",
            "引力魔方花费",
        ]
        # 粒度原样交给 service，导出不再自己分桶、相加
        assert store_calls == [{"granularity": "week"}]
        assert store_headers[7:] == ["下单转化率", "成交人数"]
        assert grouped_store_rows == [
            [
                date(2026, 8, 3),
                10,
                Decimal("20"),
                2,
                Decimal("3"),
                Decimal("1"),
                None,
                None,
                Decimal("2"),
            ],
            [
                date(2026, 8, 4),
                5,
                Decimal("8"),
                1,
                None,
                Decimal("2"),
                Decimal("4"),
                Decimal("0.63"),
                Decimal("1"),
            ],
        ]

        response = await service.export(
            uuid4(),
            "production",
            period,
            granularity="day",
            field_ctx=_SUPER_CTX,
            perms=_ALL_PERMS,
        )
        body = b"".join([chunk async for chunk in response.body_iterator])
        worksheet = load_workbook(io.BytesIO(body), data_only=False).active
        # 危险表头是唯一的动态 extra 列，落在固定列之后（最后一列）
        last_col = worksheet.max_column
        assert worksheet.cell(1, last_col).value == f"'{dangerous_header}"
        assert worksheet.cell(1, last_col).data_type != "f"
        # 第 2 列简称、第 3 列名称，两列都是用户可编辑的文字，都不能被当成公式执行
        assert worksheet.cell(2, 2).value == f"'{dangerous_short}"
        assert worksheet.cell(2, 2).data_type != "f"
        assert worksheet.cell(2, 3).value == f"'{dangerous_text}"
        assert worksheet.cell(2, 3).data_type != "f"

        store_response = await service.export(
            uuid4(),
            "store-daily",
            period,
            granularity="week",
            field_ctx=_SUPER_CTX,
            perms=_ALL_PERMS,
        )
        store_body = b"".join([chunk async for chunk in store_response.body_iterator])
        store_sheet = load_workbook(io.BytesIO(store_body), data_only=False).active
        assert store_calls[-1] == {"granularity": "week"}
        assert store_sheet.max_row == 3  # 表头 + service 给的两行，没有再合并
        # 第 8 列「下单转化率」：None 写成空单元格，有值的是 Excel 数值
        assert store_sheet.cell(1, 8).value == "下单转化率"
        assert store_sheet.cell(2, 8).value in (None, "")
        assert store_sheet.cell(3, 8).value == 0.63

    @pytest.mark.parametrize("granularity", ["day", "week", "month"])
    async def test_store_follows_page_columns(
        self, monkeypatch: pytest.MonkeyPatch, granularity: str
    ) -> None:
        """导出类型 ``store``（7e R6 S11，R4 评审 L5）：表头 = 周期列 + 页面列定义 + extra；
        周 / 月写行上实际起止（区间从周三起，首行写周三不写周一）；文件名带粒度。"""
        from app.modules.report import export_service as module

        def store_row(date_from: date, date_to: date, **over: object) -> StoreReportRow:
            # 每个指标列给不同的值，逐列能对上
            values: dict[str, object] = {
                c.key: (i + 1 if c.kind == "count" else Decimal(f"{i + 1}.25"))
                for i, c in enumerate(STORE_COLUMNS)
            }
            values.update(over)
            return StoreReportRow(
                date_from=date_from,
                date_to=date_to,
                refund_only_days=1,
                day_count=5,
                **values,  # type: ignore[arg-type]
            )

        wed, sun = date(2026, 9, 2), date(2026, 9, 6)
        items = [
            store_row(wed, sun, extra={"成交人数": "2", "下单转化率": None}),
            store_row(
                date(2026, 9, 7),
                date(2026, 9, 9),
                ad_spend_total=None,
                net_roi=None,
                extra={"成交人数": "1"},
            ),
        ]
        calls: list[tuple[object, ...]] = []

        class FakeStoreDailyService:
            def __init__(self, _session: object) -> None:
                pass

            async def get_store_report(self, *args: object) -> StoreReport:
                calls.append(args[1:])
                return StoreReport(
                    granularity=args[2],  # type: ignore[arg-type]
                    date_from=wed,
                    date_to=date(2026, 9, 9),
                    items=items,
                    recorded_days=2,
                    total_days=10,
                )

        monkeypatch.setattr(module, "StoreDailyService", FakeStoreDailyService)
        period = (wed, date(2026, 9, 9))
        response = await module.ReportExportService(None).export(  # type: ignore[arg-type]
            uuid4(),
            "store",
            period,
            granularity=granularity,
            field_ctx=_SUPER_CTX,
            perms=_ALL_PERMS,
        )
        assert calls == [(period, granularity)]
        assert response.headers["content-disposition"] == (
            f'attachment; filename="store-{granularity}_2026-09-02_2026-09-09.xlsx"'
        )
        body = b"".join([chunk async for chunk in response.body_iterator])
        wb = load_workbook(io.BytesIO(body), data_only=False)
        assert wb.sheetnames == [f"store-{granularity}"]
        sheet = list(wb.active.iter_rows(values_only=True))
        period_headers = ("日期",) if granularity == "day" else ("实际起", "实际止")
        metric_headers = tuple(c.header for c in STORE_COLUMNS)
        assert sheet[0] == (*period_headers, *metric_headers, "下单转化率", "成交人数")
        assert "商品访客数合计" in sheet[0] and "访客数" not in sheet[0]
        assert len(sheet) == 3
        n = len(period_headers)
        first, second = sheet[1], sheet[2]
        assert first[0].date() == wed
        if n == 2:
            assert (first[1].date(), second[0].date(), second[1].date()) == (
                sun,
                date(2026, 9, 7),
                date(2026, 9, 9),
            )
        expected = tuple(
            float(v) if isinstance(v, Decimal) else v
            for v in (getattr(items[0], c.key) for c in STORE_COLUMNS)
        )
        assert first[n : n + len(STORE_COLUMNS)] == expected
        cells = dict(zip(sheet[0], second, strict=True))
        # None → 空单元格；extra 十进制字符串 → Excel 数值
        assert cells["全站推消耗"] is None and cells["净投产"] is None
        assert cells["成交人数"] == 1 and cells["下单转化率"] is None
        assert dict(zip(sheet[0], first, strict=True))["成交人数"] == 2

    async def test_store_rejects_year(self) -> None:
        """``store`` 只有日 / 周 / 月三页：年粒度 400（旧 ``store-daily`` 仍收 year）。"""
        from app.modules.report import export_service as module

        with pytest.raises(module.ReportExportTypeInvalidError):
            await module.ReportExportService(None).export(  # type: ignore[arg-type]
                uuid4(),
                "store",
                (date(2026, 9, 1), date(2026, 9, 2)),
                granularity="year",
                field_ctx=_SUPER_CTX,
                perms=_ALL_PERMS,
            )

    async def test_work_progress_three_sheets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """工作进度导出三个 sheet（7e R5）：PR 进度 / 单品汇总（末行未归属）/ 周明细（实际起止）。

        三个 service 方法都拿到同一份区间、季节、``PR_SCOPE_ALL``；单品一次取全部（不只第一页）。
        """
        from app.modules.report import export_service as module

        # 每个指标给不同的值，逐列能对上；cost（旧样品成本）给一个不会出现的值
        metrics: dict[str, object] = {
            c.key: i + 1 for i, c in enumerate(WORK_PROGRESS_METRIC_COLUMNS)
        }
        metrics["promo_cost"] = Decimal("400.00")
        old_cost = Decimal("987.65")
        pr_row = SimpleNamespace(pr_name="PR甲", cost=old_cost, **metrics)
        goods_rows = [
            SimpleNamespace(display_name=name, goods_title=title, cost=old_cost, **metrics)
            for name, title in (
                ("短裙", "2026秋 A 字半身短裙"),
                ("外套", "2026秋 羊毛双面呢外套"),
                ("=1+1", "=1+1"),
            )
        ]
        unassigned = SimpleNamespace(
            display_name="未归属商品", goods_title=None, cost=old_cost, **metrics
        )
        week_rows = [
            SimpleNamespace(
                week_start=date(2026, 7, 27),
                date_from=date(2026, 7, 29),
                date_to=date(2026, 8, 2),
                cost=old_cost,
                **metrics,
            ),
            SimpleNamespace(
                week_start=date(2026, 8, 3),
                date_from=date(2026, 8, 3),
                date_to=date(2026, 8, 7),
                cost=old_cost,
                **metrics,
            ),
        ]
        calls: dict[str, list[tuple[tuple[object, ...], dict[str, object]]]] = {}

        class FakeWorkProgressService:
            def __init__(self, _session: object) -> None:
                pass

            async def get_for_range(self, *args: object, **kwargs: object) -> list[object]:
                calls.setdefault("pr", []).append((args[1:], kwargs))
                return [pr_row]

            async def get_goods(self, *args: object, **kwargs: object) -> object:
                calls.setdefault("goods", []).append((args[1:], kwargs))
                lo = int(kwargs["offset"])  # type: ignore[call-overload]
                hi = lo + int(kwargs["limit"])  # type: ignore[call-overload]
                return SimpleNamespace(
                    total=len(goods_rows), items=goods_rows[lo:hi], unassigned=unassigned
                )

            async def get_weeks(self, *args: object, **kwargs: object) -> object:
                calls.setdefault("weeks", []).append((args[1:], kwargs))
                return SimpleNamespace(items=week_rows)

        monkeypatch.setattr(module, "WorkProgressService", FakeWorkProgressService)
        period = (date(2026, 7, 29), date(2026, 8, 7))
        response = await module.ReportExportService(None).export(  # type: ignore[arg-type]
            uuid4(),
            "work-progress",
            period,
            seasons=["2026秋"],
            field_ctx=_SUPER_CTX,
            perms=_ALL_PERMS,
        )
        assert response.headers["content-disposition"] == (
            'attachment; filename="work-progress_2026-07-29_2026-08-07.xlsx"'
        )
        body = b"".join([chunk async for chunk in response.body_iterator])
        wb = load_workbook(io.BytesIO(body), data_only=False)
        assert wb.sheetnames == ["PR 进度", "单品汇总", "周明细"]
        sheets = {name: list(wb[name].iter_rows(values_only=True)) for name in wb.sheetnames}

        # 表头：有「推广成本」，没有旧的「成本」；指标段三表相同
        assert sheets["PR 进度"][0] == PR_HEADERS
        assert sheets["单品汇总"][0] == GOODS_HEADERS
        assert sheets["周明细"][0] == WEEK_HEADERS
        assert "推广成本" in PR_HEADERS and "成本" not in PR_HEADERS
        assert GOODS_HEADERS[:2] == ("商品", "商品全称")
        assert PR_HEADERS[1:] == GOODS_HEADERS[2:] == WEEK_HEADERS[3:]
        expected_metrics = tuple(
            float(v) if isinstance(v, Decimal) else v for v in metrics.values()
        )
        assert sheets["PR 进度"][1:] == [("PR甲", *expected_metrics)]
        assert 987.65 not in sheets["PR 进度"][1]

        # 单品：全部商品（不止一页）+ 末行未归属；第 2 列商品全称（未归属留空）；危险文本照样转义；
        # 不导商品编码
        assert [r[:2] for r in sheets["单品汇总"][1:]] == [
            ("短裙", "2026秋 A 字半身短裙"),
            ("外套", "2026秋 羊毛双面呢外套"),
            ("'=1+1", "'=1+1"),
            ("未归属商品", None),
        ]
        assert all(r[2:] == expected_metrics for r in sheets["单品汇总"][1:])

        # 周：所在周的周一 + 实际起止（首周起 = 区间起，不是周一）
        week_dates = [tuple(c.date() for c in r[:3]) for r in sheets["周明细"][1:]]
        assert week_dates == [
            (date(2026, 7, 27), date(2026, 7, 29), date(2026, 8, 2)),
            (date(2026, 8, 3), date(2026, 8, 3), date(2026, 8, 7)),
        ]

        # 三个方法：同一区间、季节透传、看全部 PR
        for kind in ("pr", "goods", "weeks"):
            [(args, kwargs)] = calls[kind]
            assert args == period, kind
            assert kwargs["seasons"] == ["2026秋"], kind
            assert kwargs["scope"] is PR_SCOPE_ALL, kind
            assert kwargs["field_ctx"] is _SUPER_CTX, kind
        assert calls["goods"][0][1]["offset"] == 0

    async def test_work_progress_goods_without_unassigned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """选了季节时没有未归属（service 给 None）：单品 sheet 只有商品行，不多一行空的。"""
        from app.modules.report import export_service as module

        metrics = {c.key: 1 for c in WORK_PROGRESS_METRIC_COLUMNS}
        goods = SimpleNamespace(display_name="短裙", goods_title="A 字半身短裙", **metrics)

        class FakeWorkProgressService:
            def __init__(self, _session: object) -> None:
                pass

            async def get_for_range(self, *_a: object, **_k: object) -> list[object]:
                return []

            async def get_goods(self, *_a: object, **_k: object) -> object:
                return SimpleNamespace(total=1, items=[goods], unassigned=None)

            async def get_weeks(self, *_a: object, **_k: object) -> object:
                return SimpleNamespace(items=[])

        monkeypatch.setattr(module, "WorkProgressService", FakeWorkProgressService)
        response = await module.ReportExportService(None).export(  # type: ignore[arg-type]
            uuid4(),
            "work-progress",
            (date(2026, 8, 3), date(2026, 8, 4)),
            seasons=["2026秋"],
            field_ctx=_SUPER_CTX,
            perms=_ALL_PERMS,
        )
        body = b"".join([chunk async for chunk in response.body_iterator])
        wb = load_workbook(io.BytesIO(body), data_only=False)
        assert [r[0] for r in wb["单品汇总"].iter_rows(values_only=True)] == ["商品", "短裙"]
        # 没数据的 sheet 也照样有表头
        assert wb["PR 进度"].max_row == wb["周明细"].max_row == 1


def test_work_progress_metric_columns_cover_model() -> None:
    """指标段 + 旧 ``cost`` = ``WorkProgressMetrics`` 全部字段：模型加列漏了导出就红。"""
    keys = [c.key for c in WORK_PROGRESS_METRIC_COLUMNS]
    assert len(keys) == len(set(keys))
    assert {*keys, "cost"} == set(WorkProgressMetrics.model_fields)
    headers = [c.header for c in WORK_PROGRESS_METRIC_COLUMNS]
    assert len(headers) == len(set(headers))
