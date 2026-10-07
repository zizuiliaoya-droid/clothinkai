"""Celery 路径的 FK 目标表（台账：新 model 的 FK 目标表要显式 import，设计 §3.5）。

worker 只 ``import app.tasks.import_tasks``，不经过 ``app.main``：如果 ``user`` 表的 model 没被
import，``ImportConflict`` 的外键在 flush 时才报 ``NoReferencedTableError``——只在 Celery 里炸，
HTTP 进程与测试进程都看不出来。所以在干净的子进程里验。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

_SCRIPT = textwrap.dedent(
    """
    import app.tasks.import_tasks  # noqa: F401  Celery worker 的入口
    from uuid import uuid4

    from sqlalchemy import insert
    from sqlalchemy.dialects import postgresql

    from app.modules.importer.models import ImportConflict

    # 每个外键的目标表都要能解析（缺 model 时这里抛 NoReferencedTableError）
    targets = sorted(fk.column.table.name for fk in ImportConflict.__table__.foreign_keys)
    stmt = insert(ImportConflict).values(
        id=uuid4(), tenant_id=uuid4(), source="manual_blogger", object_type="blogger",
        object_id=uuid4(), object_key="k", object_label="l", kind="fields",
        created_by=uuid4(), resolved_by=uuid4(),
    )
    stmt.compile(dialect=postgresql.dialect())
    print("FK_TARGETS=" + ",".join(targets))
    """
)


@pytest.mark.integration
def test_import_conflict_fk_targets_resolve_in_celery_path() -> None:
    proc = subprocess.run(  # 固定脚本、sys.executable
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "NoReferencedTableError" not in proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("FK_TARGETS="))
    assert line == "FK_TARGETS=import_batch,tenant,user,user"
