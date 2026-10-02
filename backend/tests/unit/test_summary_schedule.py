"""汇总刷新任务的注册与调度（纯规则，不连库）。

一个没注册进 ``autodiscover_tasks`` 的任务 ``.delay()`` 时才会报
「Received unregistered task」，而 Beat 里拼错 task 名则是**静默**的：Beat 照常
发消息，worker 收不到对应任务就丢掉，日志里只有一行 unregistered，报表停在旧数字上
没人发现。所以这里把名字与调度钉死。
"""

from __future__ import annotations

from app.core.celery_app import celery_app
from app.tasks.summary_tasks import REFRESH_WINDOW_DAYS, refresh_report_summaries

_BEAT_KEY = "refresh-report-summaries-hourly"
_TASK_NAME = "app.tasks.summary_tasks.refresh_report_summaries"


class TestTaskRegistration:
    def test_task_name_matches_beat_entry(self) -> None:
        """Beat 里写的 task 名必须和装饰器上的一致。

        拼错不会报错，只会让定时刷新永远不执行。
        """
        assert refresh_report_summaries.name == _TASK_NAME
        assert celery_app.conf.beat_schedule[_BEAT_KEY]["task"] == _TASK_NAME

    def test_module_is_autodiscovered(self) -> None:
        """模块要在 autodiscover 列表里，否则 worker 侧注册不上这个任务。"""
        from app.core import celery_app as mod

        src = mod.__file__ or ""
        assert src, "拿不到 celery_app 模块路径"
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        assert '"app.tasks.summary_tasks"' in content

    def test_runs_on_report_queue(self) -> None:
        """刷新要走 report 队列，不能挤 default。

        default 队列上有催发扫描、企微投递、异常预警；刷新是分钟级的重活，
        混进去会拖慢那几个对时效敏感的任务。
        """
        assert refresh_report_summaries.queue == "report"
        assert celery_app.conf.beat_schedule[_BEAT_KEY]["options"]["queue"] == "report"
        assert "report" in celery_app.conf.task_queues


class TestSchedule:
    def test_hourly_at_offset_minute(self) -> None:
        """每小时一次，且不在整点或 15 分的倍数上。

        整点是异常预警，*/15 是采集恢复。三个任务都要连库，撞在同一分钟会让
        连接池吃紧。
        """
        schedule = celery_app.conf.beat_schedule[_BEAT_KEY]["schedule"]
        minutes = schedule.minute
        assert len(minutes) == 1, f"应该每小时只跑一次，实际 minute={minutes}"
        minute = next(iter(minutes))
        assert minute % 15 != 0, f"minute={minute} 与异常预警/采集恢复撞车"
        # hour 不限 → 每小时
        assert len(schedule.hour) == 24

    def test_window_covers_previous_month(self) -> None:
        """滚动窗口至少要能覆盖「上个月整月」。

        月汇总是按整月桶算的：窗口不足 31 天时，每月 1 号那次刷新拿不到上月的
        完整数据，上月那一行会被半个月的数字覆盖。
        """
        assert REFRESH_WINDOW_DAYS >= 31
