"""Adapter contract shared by platform collectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from threading import Event

from worker.models import Credential, TaskAssignment


class AdapterError(RuntimeError):
    """A collector cannot safely complete its task."""


class CollectorAdapter(ABC):
    @abstractmethod
    def collect(
        self,
        task: TaskAssignment,
        credential: Credential,
        output_dir: Path,
        stop_event: Event | None = None,
    ) -> Path:
        """Collect one task and return an existing non-empty output file."""
