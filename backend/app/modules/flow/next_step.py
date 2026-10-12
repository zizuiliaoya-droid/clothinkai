"""时间线顶部的「下一步：等 XX 做 XX」（流程线设计 6.5 ``next_step``、6.6）。

设计写「由 5.4 的矩阵算」，但矩阵只回答「这个人能不能做」，没有「现在等谁」这一列；这里按阶段 / 状态给一张静态表
（计划 D6 的偏差）。推广单 22 个阶段（``stage_calculator.PROMOTION_STAGES``）与谈款 7 态各一格，终态为 ``None``；
单测守两张表的键与阶段名、状态枚举一一对应。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NextStep:
    text: str
    who: str


_PR = "PR"

PROMOTION_NEXT_STEP: dict[str, NextStep | None] = {
    "待推送仓库": NextStep("等 PR 把发货单推送给仓库", _PR),
    "待仓库发货": NextStep("等待仓库打单发货", "仓库"),
    "档期内": NextStep("等博主按约定时间发布，发布后由 PR 登记发布链接", _PR),
    "催发": NextStep("已到催发时间，等 PR 催博主发布", _PR),
    "重要催发": NextStep("博主拖得较久，等 PR 重点催发", _PR),
    "超时": NextStep("发布已超时，等 PR 跟进（继续催发或召回）", _PR),
    "未排期": NextStep("还没有约定发布时间，等 PR 补上排期", _PR),
    "待主管审核": NextStep("等主管审核推广", "主管"),
    "推广驳回 · 待重提": NextStep("推广审核被驳回，等 PR 修改后重提", _PR),
    "待财务付款": NextStep("等财务付款", "财务"),
    "结款驳回 · 待重提": NextStep("结款被驳回，等 PR 修改后重提", _PR),
    "待传结款截图": NextStep("财务已付款，等 PR 上传结款聊天截图", _PR),
    "待满 7 天": NextStep("等发布满 7 天后由 PR 录数据", _PR),
    "待录 7 天数据": NextStep("发布已满 7 天，等 PR 录 7 天数据", _PR),
    "待复盘": NextStep("等 PR 写复盘", _PR),
    "召回中": NextStep("召回中，等博主寄回、PR 登记召回结果", _PR),
    "已完结": None,
    "已完结 · 召回": None,
    "已完结 · 不合作": None,
    "已停用": None,
    "已删除": None,
    "状态异常": NextStep("状态对不上，请联系管理员排查", "管理员"),
}

NEGOTIATION_NEXT_STEP: dict[str, NextStep | None] = {
    "草稿": NextStep("等 PR 提交审核", _PR),
    "主管待审": NextStep("等主管审核", "主管"),
    "审核驳回": None,  # 终态：重谈用「复制为新谈款」
    "待老板审核": NextStep("等老板（管理员）定稿", "管理员"),
    "老板退回": NextStep("老板退回，等 PR 修改后重新提交", _PR),
    "待补收货地址": NextStep("等 PR 录完收货信息并确认，确认后生成推广单", _PR),
    "已定稿": None,  # 推广单已生成，下一步看推广单的阶段
}


def next_step(*, promotion_stage: str | None, negotiation_status: str | None) -> NextStep | None:
    """线上有推广单（且看得到）按推广阶段；否则按谈款状态。表里没有的值不提示（不报错）。"""
    if promotion_stage is not None:
        return PROMOTION_NEXT_STEP.get(promotion_stage)
    if negotiation_status is not None:
        return NEGOTIATION_NEXT_STEP.get(negotiation_status)
    return None


__all__ = ["NEGOTIATION_NEXT_STEP", "PROMOTION_NEXT_STEP", "NextStep", "next_step"]
