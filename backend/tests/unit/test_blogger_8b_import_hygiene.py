"""8b 代码边界（fix-blog8b F4，评审 N1）。

- 059 迁移不 import app 代码：以后 app 里改名 / 挪模块，从零升级（CI 每次都跑）不会炸
- 灰豚抖音 adapter 只用 ``adapters/blogger.py`` 的公开名：改名时 import 处直接报错，不会悄悄依赖私有实现
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION = _BACKEND / "alembic" / "versions" / "059_8b_blogger_library.py"
_DOUYIN = _BACKEND / "app" / "modules" / "importer" / "adapters" / "blogger_douyin.py"


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", [alias.name for alias in node.names]))
    return out


def test_migration_059_does_not_import_app() -> None:
    bad = [m for m, _ in _imports(_MIGRATION) if m == "app" or m.startswith("app.")]
    assert bad == []


def test_douyin_adapter_uses_public_names_only() -> None:
    private = [
        (m, n)
        for m, names in _imports(_DOUYIN)
        if m.startswith("app.")
        for n in names
        if n.startswith("_")
    ]
    assert private == []
