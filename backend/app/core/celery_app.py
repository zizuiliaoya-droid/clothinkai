"""Celery 应用与 Beat 调度配置。

队列拆分（按 shared-infrastructure.md 第 6 节）：
- default: 通用
- backup: 备份相关（U01 启用）
- wecom: 企微（U07 启用）
- crawler: 采集（U13 启用）
- monitor: 监控告警（U15 启用）
- report: 报表预聚合（U14 启用）
- ai: AI 调用（U18 启用）
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

# ---------------------------------------------------------------------------
# Celery 应用
# ---------------------------------------------------------------------------

celery_app = Celery(
    "clothing_erp",
    broker=settings.REDIS_URL_CELERY_BROKER,
    backend=settings.REDIS_URL_CELERY_BACKEND,
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 分钟硬超时
    task_soft_time_limit=25 * 60,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    task_default_queue="default",
    task_queues={
        "default": {},
        "backup": {},
        "crawler": {},
        "report": {},
    },
)

# ---------------------------------------------------------------------------
# 任务自动发现
# ---------------------------------------------------------------------------

# 后续单元会增加更多 task module
celery_app.autodiscover_tasks(
    [
        "app.tasks.backup_tasks",
        "app.tasks.cleanup_tasks",
        "app.tasks.import_tasks",  # U06a：run_import_batch（NF-4，否则 .delay 找不到任务）
        "app.tasks.wecom_tasks",  # U07：scan_and_dispatch_urge + execute_wecom_message
        "app.tasks.blogger_tasks",  # U11：recompute_all_blogger_tags
        "app.tasks.crawler_tasks",  # U13：schedule_daily_tasks
        "app.tasks.report_tasks",  # U14：precompute_report_cache（占位）
        "app.tasks.summary_tasks",  # PRD 模块三：refresh_report_summaries（5 张汇总表）
        "app.tasks.urge_tasks",  # PRD 改动 2：scan_urge_tasks（催发任务，独立于企微）
    ]
)

# ---------------------------------------------------------------------------
# Beat 调度（U01 范围）
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    # 每日 03:00 备份（NFR04）
    "backup-database-daily": {
        "task": "app.tasks.backup_tasks.backup_database",
        "schedule": crontab(hour=3, minute=0),
        "options": {"queue": "backup"},
    },
    # 每日 04:00 清理过期备份
    "cleanup-expired-backups-daily": {
        "task": "app.tasks.backup_tasks.cleanup_expired_backups",
        "schedule": crontab(hour=4, minute=0),
        "options": {"queue": "backup"},
    },
    # 每日 04:30 清理过期 refresh_token
    "cleanup-expired-refresh-tokens-daily": {
        "task": "app.tasks.cleanup_tasks.cleanup_expired_refresh_tokens",
        "schedule": crontab(hour=4, minute=30),
        "options": {"queue": "default"},
    },
    # 每月 1 日 04:30 归档 audit_log
    "archive-audit-logs-monthly": {
        "task": "app.tasks.cleanup_tasks.archive_audit_logs",
        "schedule": crontab(hour=4, minute=30, day_of_month="1"),
        "options": {"queue": "backup"},
    },
    # PRD 改动 2 催发任务扫描。00:30 UTC = 08:30 北京（上面 enable_utc=True，
    # 所有 crontab 的 hour 都是 UTC），正好上班时看到当天要催的单。
    # 排在企微投递之前：先把任务与留痕落下来，企微再去发消息，这样企微没配置
    # 也有催发记录。租户列表取自 tenant 而不是 wecom_config —— 后者生产是 0 行。
    "urge-task-scan": {
        "task": "app.tasks.urge_tasks.scan_urge_tasks",
        "schedule": crontab(hour=0, minute=30),
        "options": {"queue": "default"},
    },
    # 每日 09:00 企微催发扫描（U07，与备份/清理错峰）
    "wecom-urge-scan": {
        "task": "app.tasks.wecom_tasks.scan_and_dispatch_urge",
        "schedule": crontab(hour=9, minute=0),
        "options": {"queue": "default"},
    },
    # U13 每日 02:00 采集任务派发（crawler 队列，与 03:00 备份错峰）
    "crawler-daily-schedule": {
        "task": "app.tasks.crawler_tasks.schedule_daily_tasks",
        "schedule": crontab(hour=2, minute=0),
        "options": {"queue": "crawler"},
    },
    # U13 每 15 分钟恢复已提交但 Celery 投递失败/中断的采集导入批次。
    "crawler-recover-stalled-imports": {
        "task": "app.tasks.crawler_tasks.recover_stalled_crawler_imports",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "crawler"},
    },
    # U15 每小时异常预警监控（default 队列，与 09:00 催发/02:00 采集错峰）
    "check-anomaly-hourly": {
        "task": "app.tasks.wecom_tasks.check_anomaly_and_alert",
        "schedule": crontab(minute=0),
        "options": {"queue": "default"},
    },
    # PRD 模块三：每小时刷新 5 张报表汇总表，滚动窗口最近 31 天。
    # minute=20 是为了避开整点的异常预警与 */15 的采集恢复 —— 三个任务都要连库，
    # 撞在同一分钟会让连接池吃紧。
    # 02:00 的采集导入期间也会刷一次，那时可能读到部分数据；下一个小时会再刷，
    # 最终一致。真要「导入完成即刷新」得在 importer 里挂回调，留给后续批次。
    "refresh-report-summaries-hourly": {
        "task": "app.tasks.summary_tasks.refresh_report_summaries",
        "schedule": crontab(minute=20),
        "options": {"queue": "report"},
    },
    # U11 博主标签批量重算（选装）：默认注释，需要时取消注释启用
    # "recompute-blogger-tags-daily": {
    #     "task": "app.tasks.blogger_tasks.recompute_all_blogger_tags",
    #     "schedule": crontab(hour=2, minute=0),
    #     "options": {"queue": "default"},
    # },
}
