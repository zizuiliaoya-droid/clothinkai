"""汇总覆盖失效 helper（7e R2，报表视图设计 3.3）。

口径相关的业务数据变了（R2：平台链接启停 / 改渠道 / 改归属 / 改款式 / 删除；R7：平台指标系数），
汇总表里按旧口径算出的数字就和实时分叉了。这里在**调用方自己的业务事务里**清掉该租户的
``report_summary_coverage``：读取侧看不到覆盖就回到实时，下一个整点刷新再按新口径写回。
和业务改动一起提交、一起回滚。

为什么要**阻塞**等刷新锁，而不是像刷新那样 try：正在跑的刷新拿着旧链接状态 / 旧系数在算，
如果我们不等它、先删了覆盖，它会在之后提交，把旧数字连同覆盖记录一起写回去。刷新锁本身是
try，不会反过来等我们，所以不会死锁。刷新在拿到锁之后才读链接与配置，锁释放后的下一次刷新
一定读到新值。

等锁最多 ``LOCK_TIMEOUT``（生产一次完整刷新 0.27 s，见简报 A4）；超时抛
``SummaryRefreshBusyError``（409 ``REPORT_SUMMARY_REFRESH_BUSY``），调用方整事务回滚。

本模块只依赖 sqlalchemy 与 ``report.exceptions``：product 模块要引它，不能把 report 的
仓储 / 服务一起拖进来成环。
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.report.exceptions import SummaryRefreshBusyError

log = logging.getLogger(__name__)

# 等刷新锁的上限。模块常量而不是参数：测试 monkeypatch 成几百毫秒
LOCK_TIMEOUT = "30s"

# PostgreSQL lock_not_available：等锁超过 lock_timeout
_LOCK_NOT_AVAILABLE = "55P03"

_BUSY_MESSAGE = "报表正在刷新，请稍后再保存"

_GET_TIMEOUT_SQL = text("SELECT current_setting('lock_timeout')")
# is_local = true：只作用于当前事务，事务结束自动作废
_SET_TIMEOUT_SQL = text("SELECT set_config('lock_timeout', CAST(:value AS text), true)")
# 与刷新同一个键；刷新那边是 pg_try_advisory_xact_lock，这里是阻塞版
_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))")
_DELETE_SQL = text("DELETE FROM report_summary_coverage WHERE tenant_id = CAST(:tenant_id AS uuid)")


def summary_lock_key(tenant_id: UUID) -> str:
    """租户级汇总锁的键。刷新与失效共用：两边必须锁同一个键。

    键带命名空间前缀，与 import_tasks 按 batch_id 取的锁不会撞
    （两边都是 hashtextextended 到 bigint 键空间，字符串不同即可）。
    """
    return f"report_summary:{tenant_id}"


def _sqlstate(exc: DBAPIError) -> str | None:
    """取驱动异常的 SQLSTATE。asyncpg 适配层的异常本身带 ``sqlstate``，原始异常在 ``__cause__``。"""
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        state = getattr(candidate, "sqlstate", None)
        if state:
            return str(state)
    return None


async def acquire_summary_refresh_lock(session: AsyncSession, *, tenant_id: UUID) -> None:
    """先串行再读现值：在当前事务拿同一租户的刷新锁，不清覆盖、不 commit。

    等刷新锁超时抛 ``SummaryRefreshBusyError``；此时事务已中止，调用方必须回滚。
    """
    original = (await session.execute(_GET_TIMEOUT_SQL)).scalar_one()
    await session.execute(_SET_TIMEOUT_SQL, {"value": LOCK_TIMEOUT})
    try:
        await session.execute(_LOCK_SQL, {"key": summary_lock_key(tenant_id)})
    except DBAPIError as exc:
        if _sqlstate(exc) == _LOCK_NOT_AVAILABLE:
            raise SummaryRefreshBusyError(_BUSY_MESSAGE) from exc
        raise
    # 拿到锁就还原：业务事务后面的语句不该受 30s 等锁上限影响
    await session.execute(_SET_TIMEOUT_SQL, {"value": original})


async def invalidate_summary_coverage(
    session: AsyncSession, *, tenant_id: UUID, reason: str
) -> int:
    """在当前事务里拿刷新锁并清覆盖，返回删掉的行数。不 commit。

    已持有同一锁时事务内重入，保留本接口给其他调用方的锁保护。
    等锁超时抛 ``SummaryRefreshBusyError``；调用方必须回滚。
    """
    await acquire_summary_refresh_lock(session, tenant_id=tenant_id)
    result = await session.execute(_DELETE_SQL, {"tenant_id": tenant_id})
    rows = int(getattr(result, "rowcount", 0) or 0)
    log.info(
        "report_summary_coverage_invalidated",
        extra={"tenant_id": str(tenant_id), "reason": reason, "rows": rows},
    )
    return rows


__all__ = [
    "LOCK_TIMEOUT",
    "acquire_summary_refresh_lock",
    "invalidate_summary_coverage",
    "summary_lock_key",
]
