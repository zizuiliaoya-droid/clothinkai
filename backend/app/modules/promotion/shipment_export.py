"""仓库待打单导出 xlsx（流程线 7.4，7c-2）。

一行一个商品明细（套装 2 行，收件信息重复，第一列是推广单内部编码，方便合并）；没有明细的旧单写一行，
颜色列放 ``legacy_color_spec`` 原文。编码列保留（ad 二：导出保留编码，对账用）。写法照
``report/export_service.py``：openpyxl write_only、文本以公式字符开头的转义成字面量。纯函数，不碰库。
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook

SHIPMENT_EXPORT_HEADERS: tuple[str, ...] = (
    "内部编码",
    "推送时间",
    "收件人",
    "电话",
    "地址",
    "款式编码",
    "商品编码",
    "SKU 编码",
    "商品简称",
    "颜色",
    "尺码",
    "数量",
)

EXPORT_ROW_LIMIT = 5000
"""一次最多导出多少张推广单（11-26），超了 422 提示缩小范围。"""

_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ExportWatermark:
    """导出水印（工号 + 姓名 + 时间戳，斜纹背景 + 页眉页脚）。批次 6 实现，本轮调用方一律传 None。"""

    employee_no: str
    name: str
    issued_at: datetime


@dataclass(frozen=True)
class ShipmentExportItem:
    style_code: str
    sku_code: str
    short_name: str
    color: str
    size: str


@dataclass(frozen=True)
class ShipmentExportRow:
    """一张推广单。``style_code`` / ``sku_code`` / ``short_name`` 是没有明细时那一行用的（推广单自己的）。

    收件三项由调用方按字段规则投影好（读不到的传 None）。
    """

    internal_code: str
    ship_pushed_at: datetime | None
    receiver_name: str | None
    receiver_phone: str | None
    receiver_address: str | None
    style_code: str
    goods_code: str | None
    sku_code: str | None
    short_name: str
    legacy_color_spec: str | None
    items: tuple[ShipmentExportItem, ...]


def _cell(v: Any) -> Any:
    # Excel 会把以 =、+、-、@ 开头的字符串解释为公式；检查首个非空白字符，防止用前导空格绕过
    if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{v}"
    return v


def _pushed_at(v: datetime | None) -> str | None:
    """推送时间按北京时间写成文本（Excel 不支持带时区的日期）。"""
    return v.astimezone(_TZ).strftime("%Y-%m-%d %H:%M") if v is not None else None


def _lines(row: ShipmentExportRow) -> list[list[Any]]:
    head = [
        row.internal_code,
        _pushed_at(row.ship_pushed_at),
        row.receiver_name,
        row.receiver_phone,
        row.receiver_address,
    ]
    if not row.items:
        return [
            [
                *head,
                row.style_code,
                row.goods_code,
                row.sku_code,
                row.short_name,
                row.legacy_color_spec,
                None,
                1,
            ]
        ]
    return [
        [*head, i.style_code, row.goods_code, i.sku_code, i.short_name, i.color, i.size, 1]
        for i in row.items
    ]


def count_lines(rows: Iterable[ShipmentExportRow]) -> int:
    """导出的数据行数（不含表头）：有明细按明细数，没有算 1 行。"""
    return sum(len(r.items) or 1 for r in rows)


def build_shipment_workbook(
    rows: Iterable[ShipmentExportRow], *, watermark: ExportWatermark | None = None
) -> bytes:
    """生成 xlsx 字节。``watermark`` 留给批次 6（PRD-V1.4-实施批次.md:789-791），现在传值直接报错。"""
    if watermark is not None:
        raise NotImplementedError("导出水印随批次 6 实现")
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("仓库发货")
    ws.append(list(SHIPMENT_EXPORT_HEADERS))
    for row in rows:
        for line in _lines(row):
            ws.append([_cell(v) for v in line])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = [
    "EXPORT_ROW_LIMIT",
    "SHIPMENT_EXPORT_HEADERS",
    "ExportWatermark",
    "ShipmentExportItem",
    "ShipmentExportRow",
    "build_shipment_workbook",
    "count_lines",
]
