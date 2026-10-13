"""注释里写的迁移编号要跟着迁移文件走（PR-3 收尾评审 L3）。

流程线 M2 在 rebase 时从 061 改号成 063，四处注释漏改。按锚词找出这几处写的编号，
和 ``alembic/versions/*_negotiation_two_level.py`` 的编号逐一比；以后再改号，这条测试会提醒一起改。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]

# (相对 backend 的路径, 锚词正则：第 1 组是编号)
_REFS = (
    ("app/modules/negotiation/enums.py", r"迁移 (\d{3}) 起 7 态两级审核"),
    ("tests/conftest.py", r"流程线 M2（(\d{3})）主线号"),
    ("tests/api/test_warehouse_api.py", r"line_id 有唯一索引（(\d{3})）"),
    ("tests/integration/test_model_fk_targets_celery.py", r"流程线 M2（(\d{3})）："),
)


def _migration_number() -> str:
    files = sorted((_BACKEND / "alembic" / "versions").glob("*_negotiation_two_level.py"))
    assert len(files) == 1, files
    return files[0].name.split("_", 1)[0]


@pytest.mark.parametrize(("rel", "anchor"), _REFS, ids=[r[0] for r in _REFS])
def test_comment_refers_to_current_migration_number(rel: str, anchor: str) -> None:
    text = (_BACKEND / rel).read_text(encoding="utf-8")
    found = re.findall(anchor, text)
    assert len(found) == 1, f"{rel}: 锚词应恰好出现一次，实际 {len(found)} 次"
    assert found[0] == _migration_number(), f"{rel}: 注释写的是 {found[0]}"
