"""Synchronous HTTP client for the crawler worker API."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from threading import Event
from uuid import UUID

import httpx

from worker.models import Credential, ProtocolError, TaskAssignment


class WorkerApiError(RuntimeError):
    def __init__(self, operation: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        detail = f" ({status_code})" if status_code is not None else ""
        super().__init__(f"worker API {operation} failed{detail}")


class WorkerApiClient:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-Worker-Token": token, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )

    def __enter__(self) -> "WorkerApiClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    def _post(
        self, operation: str, path: str, statuses: set[int], **kwargs: object
    ) -> httpx.Response:
        try:
            response = self._client.post(path, **kwargs)
        except httpx.HTTPError as exc:
            raise WorkerApiError(operation) from exc
        if response.status_code not in statuses:
            raise WorkerApiError(operation, response.status_code)
        return response

    def poll(self) -> TaskAssignment | None:
        response = self._post("poll", "/api/crawler/tasks/poll", {200, 204})
        if response.status_code == 204:
            return None
        try:
            return TaskAssignment.from_json(response.json())
        except ValueError as exc:
            raise ProtocolError("poll response is not valid JSON") from exc

    def exchange(self, task_id: UUID, cred_token: str) -> Credential:
        response = self._post(
            "exchange",
            f"/api/crawler/tasks/{task_id}/exchange",
            {200},
            json={"cred_token": cred_token},
        )
        try:
            return Credential.from_json(response.json())
        except ValueError as exc:
            raise ProtocolError("exchange response is not valid JSON") from exc

    def report_success(
        self,
        task_id: UUID,
        lease_token: str,
        output_path: Path,
        *,
        stop_event: Event | None = None,
    ) -> None:
        content_type = mimetypes.guess_type(output_path.name)[0]
        for attempt in range(3):
            if stop_event is not None and stop_event.is_set():
                raise WorkerApiError("report-success-cancelled")
            try:
                with output_path.open("rb") as stream:
                    self._post(
                        "report-success",
                        f"/api/crawler/tasks/{task_id}/result",
                        {200},
                        files={
                            "lease_token": (None, lease_token),
                            "status": (None, "success"),
                            "file": (
                                output_path.name,
                                stream,
                                content_type or "application/octet-stream",
                            ),
                        },
                    )
                return
            except OSError as exc:
                raise WorkerApiError("open-result-file") from exc
            except WorkerApiError as exc:
                retryable = exc.status_code is None or exc.status_code >= 500
                if not retryable or attempt == 2:
                    raise
                delay = 0.5 * (2**attempt)
                waiter = stop_event or Event()
                if waiter.wait(delay):
                    raise WorkerApiError("report-success-cancelled") from exc

    def report_failed(
        self,
        task_id: UUID,
        lease_token: str,
        error: str,
        *,
        stop_event: Event | None = None,
    ) -> None:
        for attempt in range(3):
            if stop_event is not None and stop_event.is_set():
                raise WorkerApiError("report-failed-cancelled")
            try:
                self._post(
                    "report-failed",
                    f"/api/crawler/tasks/{task_id}/result",
                    {200},
                    files={
                        "lease_token": (None, lease_token),
                        "status": (None, "failed"),
                        "error": (None, error[:1000]),
                    },
                )
                return
            except WorkerApiError as exc:
                retryable = exc.status_code is None or exc.status_code >= 500
                if not retryable or attempt == 2:
                    raise
                delay = 0.5 * (2**attempt)
                waiter = stop_event or Event()
                if waiter.wait(delay):
                    raise WorkerApiError("report-failed-cancelled") from exc
