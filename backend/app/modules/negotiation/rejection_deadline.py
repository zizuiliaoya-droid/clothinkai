"""主管驳回后的原单重提期限；普通编辑不重置计时。"""

from datetime import datetime, timedelta

REJECTION_RETRY_WINDOW = timedelta(days=7)


def rejection_expired(reviewed_at: datetime | None, updated_at: datetime, now: datetime) -> bool:
    return (reviewed_at or updated_at) + REJECTION_RETRY_WINDOW <= now
