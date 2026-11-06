"""人工导入空模板：表头来自实际解析映射，示例不进入填报页。"""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.core.exceptions import ResourceNotFoundError
from app.modules.blogger.enums import Platform
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.adapters.blogger_audience import BloggerAudienceImportAdapter
from app.modules.importer.adapters.blogger_work import BloggerWorkImportAdapter
from app.modules.importer.adapters.promotion import PromotionImportAdapter
from app.modules.importer.exceptions import ImportMappingInvalidError
from app.modules.importer.models import FieldMapping

TEMPLATE_VERSION = "1105-v1"
TEMPLATE_SOURCES = frozenset(
    {"manual_blogger", "manual_promotion", "manual_blogger_work", "manual_blogger_audience"}
)
_REQUIRED = {
    "manual_blogger_audience": frozenset(
        {
            "platform",
            "xiaohongshu_id",
            "dimension",
            "label",
            "collected_on",
            "source",
            "completeness",
            "estimated",
        }
    ),
    "manual_blogger_work": frozenset(
        {"platform", "xiaohongshu_id", "published_on", "collected_at", "source"}
    ),
    "manual_blogger": frozenset({"xiaohongshu_id", "nickname"}),
    "manual_promotion": frozenset(
        {"style_code", "xiaohongshu_id", "quote_amount", "cooperation_date"}
    ),
}
_EXAMPLES = {
    "dimension": "年龄",
    "label": "41–50",
    "percentage": "23.5%",
    "tgi": "120",
    "collected_on": "2026-11-05",
    "completeness": "部分",
    "estimated": "否",
    "tgi_source": "来源页面的TGI及基准人群说明",
    "work_id": "demo_work_001",
    "work_url": "https://www.example.com/video/demo_work_001",
    "published_on": "2026-11-01",
    "collected_at": "2026-11-05 14:30:00",
    "source": "平台作品页人工采集",
    "like_count": "14.3w",
    "xiaohongshu_id": "demo_account_1105",
    "nickname": "示例博主（仅作说明）",
    "platform": "抖音",
    "wechat": "demo_wechat",
    "phone": "13800000000",
    "follower_count": "12500",
    "blogger_type": "KOC",
    "gender_target": "女性",
    "category_tags": "穿搭;日常",
    "quote": "300.00",
    "homepage_url": "https://www.example.com/profile/demo",
    "style_code": "已有款式编码",
    "sku_code": "已有SKU编码",
    "quote_amount": "300.00",
    "cost_snapshot": "80.00",
    "cooperation_date": "2026-11-05",
    "scheduled_publish_date": "2026-11-20",
}
_NOTES = {
    "dimension": "地域省份 / 城市级别 / 年龄 / 性别；省份和城市层级分开填写。",
    "label": "保留来源原始标签；年龄不合并41–50、50+等不同分箱。城市级别填一线/新一线/二线/三线/四线/五线/六线及以下/特区/其他。",
    "percentage": "23.5和23.5%均表示23.5%；0.235表示0.235%。仅Excel原生百分比格式按显示值读取。不填分数比例来猜测换算；缺失留空。",
    "completeness": "完整 / 部分。完整各分类均有占比且合计99%～101%（含边界）；Top5或缺分类选部分，不补齐。",
    "estimated": "是 / 否，按实际来源填写。",
    "collected_on": "YYYY-MM-DD。同账号同维度同日期合并一组；同组采集来源/完整性/估计/TGI来源须一致。",
    "tgi": "只填写实际提供的TGI，不能从占比推导；填写时TGI来源必填。",
    "tgi_source": "填写TGI的来源和基准口径。",
    "work_id": "作品ID与稳定链接至少填一项；同一作品后续采集保持相同标识方式。ID优先，不自动合并仅链接的旧记录。",
    "work_url": "作品稳定 http/https 链接；不要填临时分享短链。保留有意义的查询参数。",
    "collected_at": "YYYY-MM-DD HH:mm:ss，未带时区按北京时间。同标识同时间不同内容会报错。",
    "published_on": "作品实际发布日期 YYYY-MM-DD。",
    "source": "实际采集来源，不填系统计算的结论。",
    "xiaohongshu_id": "平台账号，只含字母、数字、_ . -；推广单须对应本租户已有博主。",
    "platform": "博主空白默认小红书；抖音等账号请选择实际平台，不能只换昵称。",
    "category_tags": "多个标签用英文分号分隔；仅写入已启用标签字典中的标签。",
    "style_code": "必须是本租户已有款式；模板不创建商品资料。",
    "sku_code": "可选，填写时须为已有 SKU 编码。",
    "content_category": "沿用当前合作形式口径；留空使用该博主原有分类。",
    "homepage_url": "完整 http/https 主页链接；不要填写昵称或账号。",
}


def require_template_source(source: str) -> None:
    if source not in TEMPLATE_SOURCES:
        raise ResourceNotFoundError("该导入来源暂不提供空模板")


def build_import_template(source: str, mapping: FieldMapping | None = None) -> bytes:
    """使用上传时同一份生效映射，不更改映射或创建任何业务记录。"""
    require_template_source(source)
    adapter: (
        BloggerImportAdapter
        | PromotionImportAdapter
        | BloggerWorkImportAdapter
        | BloggerAudienceImportAdapter
    )
    if source == "manual_blogger_audience":
        adapter = BloggerAudienceImportAdapter()
    elif source == "manual_blogger_work":
        adapter = BloggerWorkImportAdapter()
    elif source == "manual_blogger":
        adapter = BloggerImportAdapter()
    else:
        adapter = PromotionImportAdapter()
    columns: list[dict[str, Any]] = (
        mapping.mapping_config.get("columns", adapter.builtin_columns())
        if mapping is not None
        else adapter.builtin_columns()
    )
    required = _REQUIRED[source]
    if source == "manual_blogger_work" and not {"work_id", "work_url"}.intersection(
        c["target_field"] for c in columns
    ):
        raise ImportMappingInvalidError("请至少映射作品ID或作品链接")
    if not required.issubset({column["target_field"] for column in columns}):
        raise ImportMappingInvalidError(
            "当前字段映射缺少必填字段，请联系管理员补齐或恢复默认后下载模板"
        )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "填报数据"
    sheet.freeze_panes = "A2"
    notes = workbook.create_sheet("填写说明与示例")
    title = {
        "manual_blogger": "博主",
        "manual_promotion": "站外推广",
        "manual_blogger_work": "博主作品",
        "manual_blogger_audience": "博主画像",
    }[source]
    notes.append([f"{title}空模板", TEMPLATE_VERSION])
    notes.append(["映射版本", str(mapping.version) if mapping is not None else "内置默认"])
    notes.append(["填写方式", "只在第一个“填报数据”工作表填报，一行一条；本页示例不会自动导入。"])
    notes.append(
        [
            "已有数据",
            "每行记录一次完整采集快照；缺指标留空，不补零。相同作品同采集时间同内容跳过，不同内容报错。"
            if source == "manual_blogger_work"
            else "可选空白不表示清空原值；按现有判重、补空和冲突规则处理。",
        ]
    )
    notes.append(
        [
            "日期",
            "发布日期填 YYYY-MM-DD；采集时间填 YYYY-MM-DD HH:mm:ss，未带时区按北京时间。"
            if source == "manual_blogger_work"
            else "按文本填写 YYYY-MM-DD（例如 2026-11-05），不要填写带时间的日期。",
        ]
    )
    notes.append(
        ["映射变化", "若管理员修改了字段映射，请重新下载模板；旧文件仍可通过字段映射使用。"]
    )
    notes.append(
        [
            "导入方式",
            "平台和账号必须匹配本租户已有博主。只填基础计数，可用14.3w；不填比值/爆款公式。"
            if source == "manual_blogger_work"
            else "博主按平台和账号判重；推广单每个有效数据行创建一张新单。",
        ]
    )
    if source == "manual_blogger_audience":
        notes.cell(
            4,
            2,
            "一行一个分类；同账号+维度+日期整组验证提交，有错误整组失败，保留数据行定位。相同内容跳过，不同内容报错；较旧采集只归档。",
        )
        notes.cell(
            7,
            2,
            "平台和账号须匹配本租户已有博主；每次仅更新对应维度，不清空其它维度。缺失不填0，部分数据不补齐到100%。",
        )
    notes.append([])
    notes.append(["表头", "是否必填", "类型", "填写示例（勿作为业务数据）", "说明"])

    for index, column in enumerate(columns, start=1):
        field = column["target_field"]
        label = column["source_col"]
        kind = column.get("type", "str")
        cell = sheet.cell(1, index, label)
        # 自定义表头也是文本，不应被 Excel 当成公式执行。
        cell.data_type = "s"
        cell.font = Font(bold=True, color="B42318" if field in required else "FFFFFF")
        cell.fill = PatternFill("solid", fgColor="FEE4E2" if field in required else "315C4E")
        cell.comment = Comment(
            "必填" if field in required else "可选；空白不表示清空原值", "ClothinkAI"
        )
        letter = get_column_letter(index)
        dimension = sheet.column_dimensions[letter]
        dimension.width = max(18, min(36, len(label) * 2 + 4))
        # 仅格式化整列，不创建看似空白的数据行（runner 会读取已创建的行）。
        dimension.number_format = {"int": "0", "decimal": "0.00"}.get(kind, "@")
        if field == "platform":
            validation = DataValidation(
                type="list",
                formula1='"' + ",".join(p.value for p in Platform) + '"',
                allow_blank=True,
            )
            validation.errorTitle = "请选择平台"
            validation.error = "请使用下拉列表中的平台名称。"
            validation.showErrorMessage = True
            sheet.add_data_validation(validation)
            validation.add(f"{letter}2:{letter}1048576")
        notes.append(
            [
                label,
                "必填" if field in required else "可选",
                kind,
                _EXAMPLES.get(field, ""),
                (
                    "必须填写已有博主的实际平台，不使用默认平台。"
                    if source == "manual_blogger_work" and field == "platform"
                    else _NOTES.get(field, "")
                ),
            ]
        )
        notes.cell(notes.max_row, 1).data_type = "s"

    notes.freeze_panes = "A10"
    for letter, width in {"A": 24, "B": 22, "C": 14, "D": 36, "E": 75}.items():
        notes.column_dimensions[letter].width = width
    workbook.active = 0
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
