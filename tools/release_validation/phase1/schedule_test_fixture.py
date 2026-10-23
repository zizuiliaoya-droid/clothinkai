"""Serialize only local synthetic Celery objects, without starting any Celery service."""

from datetime import datetime, timedelta, timezone
import pickle

from celery.beat import ScheduleEntry
from celery.schedules import crontab, schedule


def entries_for(expected):
    entries = {}
    for index, (name, task) in enumerate(expected.items()):
        sched = (
            schedule(timedelta(minutes=15), relative=False, nowfun=None)
            if index % 2
            else crontab(minute="*/15", hour=4, day_of_month="1")
        )
        entries[name] = ScheduleEntry(
            name=name,
            task=task,
            last_run_at=datetime(2026, 10, 21, 0, 30, tzinfo=timezone.utc),
            total_run_count=index,
            schedule=sched,
            args=(),
            kwargs={},
            options={"queue": "default"},
        )
    return entries


def values_for(expected):
    return {
        "entries": pickle.dumps(entries_for(expected), protocol=4),
        "__version__": pickle.dumps("5.4.0", protocol=4),
        "tz": pickle.dumps("UTC", protocol=4),
        "utc_enabled": pickle.dumps(True, protocol=4),
    }
