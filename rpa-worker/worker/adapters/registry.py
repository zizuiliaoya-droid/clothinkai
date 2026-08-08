"""Configured platform adapter registry."""

from __future__ import annotations

from worker.adapters.base import AdapterError, CollectorAdapter
from worker.adapters.command import CommandAdapter
from worker.config import Settings

PLATFORMS = {"千牛", "万相台", "灰豚"}


class AdapterRegistry:
    def __init__(self, adapters: dict[str, CollectorAdapter]) -> None:
        self._adapters = dict(adapters)

    @classmethod
    def from_settings(cls, settings: Settings) -> "AdapterRegistry":
        commands = settings.commands or {}
        adapters = {
            platform: CommandAdapter(command)
            for platform, command in commands.items()
            if platform in PLATFORMS and command
        }
        return cls(adapters)

    def get(self, platform: str) -> CollectorAdapter:
        if platform not in PLATFORMS:
            raise AdapterError("unknown platform")
        try:
            return self._adapters[platform]
        except KeyError as exc:
            raise AdapterError("platform collector command is not configured") from exc
