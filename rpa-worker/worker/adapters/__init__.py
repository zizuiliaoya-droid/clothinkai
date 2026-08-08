"""Platform collector adapters."""

from worker.adapters.base import AdapterError, CollectorAdapter
from worker.adapters.command import CommandAdapter
from worker.adapters.registry import AdapterRegistry

__all__ = ["AdapterError", "AdapterRegistry", "CollectorAdapter", "CommandAdapter"]
