"""U06a ImportAdapterRegistry 单元测试（register / get / sources / clear）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.modules.importer.registry import ImportAdapterRegistry


class _StubAdapter:
    source = "stub"
    target_table = "stub_table"

    def parse_row(self, row: dict[str, Any], mapping: Any) -> dict[str, Any]:
        return row

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        return []

    async def upsert(
        self, parsed: dict[str, Any], *, session: Any, tenant_id: UUID, actor_id: Any
    ) -> tuple[UUID, bool]:
        return uuid4(), True


@pytest.fixture(autouse=True)
def _clear_registry():
    ImportAdapterRegistry.clear()
    yield
    ImportAdapterRegistry.clear()


def test_register_and_get():
    adapter = _StubAdapter()
    ImportAdapterRegistry.register(adapter)
    assert ImportAdapterRegistry.get("stub") is adapter


def test_get_unknown_returns_none():
    assert ImportAdapterRegistry.get("nope") is None


def test_sources_returns_registered_keys():
    ImportAdapterRegistry.register(_StubAdapter())
    assert ImportAdapterRegistry.sources() == frozenset({"stub"})


def test_register_is_idempotent_overwrite():
    a1 = _StubAdapter()
    a2 = _StubAdapter()
    ImportAdapterRegistry.register(a1)
    ImportAdapterRegistry.register(a2)
    assert ImportAdapterRegistry.get("stub") is a2
    assert len(ImportAdapterRegistry.sources()) == 1


def test_clear_empties_registry():
    ImportAdapterRegistry.register(_StubAdapter())
    ImportAdapterRegistry.clear()
    assert ImportAdapterRegistry.sources() == frozenset()
