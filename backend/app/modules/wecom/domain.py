"""U07 wecom 领域纯函数（无 DB / Session 依赖）。

- 模板变量白名单校验 + 渲染（BR-U07-21/22）
- is_important：催发紧急级别判定（复用 U04 urge 阈值）
"""

from __future__ import annotations

import re
from datetime import date

from app.modules.promotion.urge_calculator import calculate_urge_status

# 4 个白名单变量（BR-U07-21）
ALLOWED_VARS: frozenset[str] = frozenset(
    {"博主昵称", "商品简称", "预定发布日期", "剩余天数"}
)

_VAR_RE = re.compile(r"\{([^}]+)\}")


def extract_template_vars(content: str) -> list[str]:
    """提取模板中所有 ``{xxx}`` 变量名（去重保序）。"""
    seen: list[str] = []
    for m in _VAR_RE.findall(content):
        if m not in seen:
            seen.append(m)
    return seen


def validate_template_vars(content: str) -> list[str]:
    """返回非法变量列表（空=通过）。"""
    return [v for v in extract_template_vars(content) if v not in ALLOWED_VARS]


def render_template(content: str, ctx: dict[str, str]) -> str:
    """用 ctx 替换白名单变量；缺值用空串（BR-U07-22）。仅替换白名单变量。"""

    def _repl(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in ALLOWED_VARS:
            return str(ctx.get(var, ""))
        return m.group(0)  # 非白名单原样保留（保存阶段已拦截）

    return _VAR_RE.sub(_repl, content)


def is_important(
    *,
    scheduled_publish_date: date | None,
    today: date,
    publish_status: str = "未发布",
    urge_days: int = 10,
    important_days: int = 3,
) -> bool:
    """该推广是否处于"重要催发/超时"级别（决定 template_type=urge_important）。"""
    status = calculate_urge_status(
        publish_status=publish_status,
        scheduled_publish_date=scheduled_publish_date,
        today=today,
        urge_threshold_days=urge_days,
        important_threshold_days=important_days,
    )
    return status in {"重要催发", "超时"}


def build_render_ctx(
    *,
    blogger_nickname: str,
    style_short_name: str,
    scheduled_publish_date: date | None,
    today: date,
) -> dict[str, str]:
    """构造模板渲染上下文。"""
    if scheduled_publish_date is not None:
        days_left = (scheduled_publish_date - today).days
        sched = scheduled_publish_date.isoformat()
    else:
        days_left = 0
        sched = ""
    return {
        "博主昵称": blogger_nickname or "",
        "商品简称": style_short_name or "",
        "预定发布日期": sched,
        "剩余天数": str(days_left),
    }


__all__ = [
    "ALLOWED_VARS",
    "build_render_ctx",
    "extract_template_vars",
    "is_important",
    "render_template",
    "validate_template_vars",
]
