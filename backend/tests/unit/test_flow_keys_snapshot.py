"""前后端键表对齐（PR-1 评审 L3）。

``app/modules/flow/flow_keys.snapshot.json`` 是后端常量的导出：本测试守「文件 = 常量」，
前端 ``src/features/flow/keys.snapshot.test.ts`` 守「文件 = keys.ts / stageStyle.ts」，两个 CI job 各跑一半。
改了常量：``UPDATE_FLOW_SNAPSHOT=1 pytest tests/unit/test_flow_keys_snapshot.py`` 重写文件，再同步前端。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.modules.flow.matrix import ACTION_KEYS, FIELD_KEYS, KINDS, PAGE_ACTION_KEYS
from app.modules.promotion.stage_calculator import PROMOTION_STAGES, STAGE_COLUMN

SNAPSHOT = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "flow" / "flow_keys.snapshot.json"
)


def build_snapshot() -> dict[str, Any]:
    return {
        "KINDS": list(KINDS),
        "ACTION_KEYS": {k: list(ACTION_KEYS[k]) for k in KINDS},
        "PAGE_ACTION_KEYS": {k: list(PAGE_ACTION_KEYS[k]) for k in KINDS},
        "FIELD_KEYS": {k: list(FIELD_KEYS[k]) for k in KINDS},
        "PROMOTION_STAGES": list(PROMOTION_STAGES),
        "STAGE_COLUMN": {s: STAGE_COLUMN[s] for s in PROMOTION_STAGES},
    }


def render(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"


def test_snapshot_matches_constants() -> None:
    expected = build_snapshot()
    if os.environ.get("UPDATE_FLOW_SNAPSHOT") == "1":
        SNAPSHOT.write_text(render(expected), encoding="utf-8", newline="\n")
    assert SNAPSHOT.exists(), f"缺 {SNAPSHOT}：UPDATE_FLOW_SNAPSHOT=1 跑本测试生成"
    got = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    # 顺序也是契约（= 前端菜单顺序）：比 JSON 文本前先比结构，失败信息更好读
    assert (
        got == expected
    ), "flow_keys.snapshot.json 与后端常量不一致：UPDATE_FLOW_SNAPSHOT=1 重写并同步前端"
    for section in ("ACTION_KEYS", "PAGE_ACTION_KEYS", "FIELD_KEYS", "STAGE_COLUMN"):
        assert list(got[section]) == list(expected[section]), section


def test_snapshot_covers_every_kind_and_stage() -> None:
    """场景有效性：快照不是空壳——每种单据都在、推广单动作 / 字段非空、22 个阶段都有列。"""
    snap = build_snapshot()
    assert (
        set(snap["ACTION_KEYS"])
        == set(snap["PAGE_ACTION_KEYS"])
        == set(snap["FIELD_KEYS"])
        == set(KINDS)
    )
    assert snap["ACTION_KEYS"]["promotion"] and snap["FIELD_KEYS"]["promotion"]
    assert len(snap["PROMOTION_STAGES"]) == len(set(snap["PROMOTION_STAGES"])) == 22
    assert set(snap["STAGE_COLUMN"]) == set(snap["PROMOTION_STAGES"])
    assert set(snap["STAGE_COLUMN"].values()) == set("ABCDEFGH")
