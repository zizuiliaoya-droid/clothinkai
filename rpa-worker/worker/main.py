"""Task orchestration and CLI entry point."""

from __future__ import annotations

import argparse
import logging
import shutil
import signal
import tempfile
from enum import Enum
from pathlib import Path
from threading import Event

from worker.adapters import AdapterError, AdapterRegistry
from worker.client import WorkerApiClient, WorkerApiError
from worker.config import ConfigError, Settings
from worker.models import ProtocolError, TaskAssignment

log = logging.getLogger("rpa_worker")
_MAX_BACKOFF = 60.0


class RunResult(Enum):
    EMPTY = "empty"
    SUCCESS = "success"
    FAILED = "failed"


def _reason(exc: Exception) -> str:
    if isinstance(exc, (AdapterError, WorkerApiError, ProtocolError)):
        return str(exc)[:500]
    return "internal worker error"


def _task_dir(work_dir: Path, task: TaskAssignment) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"{task.task_id}-", dir=work_dir))


def _process(
    client: WorkerApiClient,
    registry: AdapterRegistry,
    settings: Settings,
    task: TaskAssignment,
    stop_event: Event | None = None,
) -> RunResult:
    directory: Path | None = None
    try:
        credential = client.exchange(task.task_id, task.cred_token)
    except Exception as exc:
        log.warning(
            "credential exchange failed task_id=%s platform=%s reason=%s",
            task.task_id,
            task.platform,
            _reason(exc),
        )
        return RunResult.FAILED

    try:
        try:
            directory = _task_dir(settings.work_dir, task)
            output = registry.get(task.platform).collect(
                task, credential, directory, stop_event
            )
        except Exception as exc:  # collection failure may be reported as task failure
            reason = _reason(exc)
            log.warning(
                "task collection failed task_id=%s platform=%s reason=%s",
                task.task_id,
                task.platform,
                reason,
            )
            # 停机优先清理本地凭据和临时目录，不再发起不可中断的同步 HTTP。
            if stop_event is None or not stop_event.is_set():
                try:
                    client.report_failed(
                        task.task_id,
                        task.cred_token,
                        reason,
                        stop_event=stop_event,
                    )
                except Exception:
                    log.warning(
                        "failed result report unavailable task_id=%s", task.task_id
                    )
            return RunResult.FAILED

        try:
            client.report_success(
                task.task_id,
                task.cred_token,
                output,
                stop_event=stop_event,
            )
        except Exception as exc:
            # 服务端可能已提交成功但响应丢失；绝不能再用 failed 覆盖成功终态。
            log.warning(
                "success result acknowledgement unavailable task_id=%s reason=%s",
                task.task_id,
                _reason(exc),
            )
            return RunResult.FAILED
        log.info("task completed task_id=%s platform=%s", task.task_id, task.platform)
        return RunResult.SUCCESS
    finally:
        del credential
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except OSError:
                log.warning(
                    "task directory cleanup failed task_id=%s", task.task_id
                )


def run_once(
    client: WorkerApiClient,
    settings: Settings,
    registry: AdapterRegistry,
    stop_event: Event | None = None,
) -> RunResult:
    """Poll and process at most one task."""
    task = client.poll()
    if task is None:
        return RunResult.EMPTY
    return _process(client, registry, settings, task, stop_event)


def loop(
    client: WorkerApiClient,
    settings: Settings,
    registry: AdapterRegistry,
    stop_event: Event,
) -> None:
    """Poll until a termination signal requests a graceful stop."""
    failures = 0
    while not stop_event.is_set():
        try:
            result = run_once(client, settings, registry, stop_event)
        except (WorkerApiError, ProtocolError) as exc:
            failures = min(failures + 1, 6)
            log.warning("poll failed reason=%s", _reason(exc))
            stop_event.wait(min(2**failures, _MAX_BACKOFF))
            continue
        if result is RunResult.EMPTY:
            failures = 0
            stop_event.wait(settings.poll_interval)
        elif result is RunResult.SUCCESS:
            failures = 0
        else:
            failures = min(failures + 1, 6)
            stop_event.wait(min(2**failures, _MAX_BACKOFF))


def _install_signal_handlers(stop_event: Event) -> None:
    def request_stop(signum: int, _frame: object) -> None:
        log.info("stop requested signal=%s", signum)
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, request_stop)


def _execute(settings: Settings, once: bool) -> int:
    settings.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    registry = AdapterRegistry.from_settings(settings)
    stop_event = Event()
    _install_signal_handlers(stop_event)
    with WorkerApiClient(
        settings.api_base_url, settings.worker_token, settings.http_timeout
    ) as client:
        if once:
            try:
                result = run_once(client, settings, registry, stop_event)
            except (WorkerApiError, ProtocolError) as exc:
                log.warning("poll failed reason=%s", _reason(exc))
                return 1
            return 1 if result is RunResult.FAILED else 0
        loop(client, settings, registry, stop_event)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secure local-command crawler worker")
    parser.add_argument(
        "--once", action="store_true", help="poll and process at most one task"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return _execute(Settings.from_env(), args.once)
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        return 2
    except OSError:
        log.error("worker filesystem initialization failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
