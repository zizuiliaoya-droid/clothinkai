"""流程线矩阵的冻结期望表（流程线设计 5.4）。

**改矩阵（``app/modules/flow/matrix.py`` 的 ``MATRICES``）必须同步改设计 5.2 / 5.3 与本表。**

- ``EXPECTED[kind][(阶段标签, persona, 行类型, 行键)] = "改" | "读" | "灰" | "隐"``，与 ``MATRICES`` 逐格比对
  （``tests/unit/test_flow_matrix.py::test_real_matrices_match_expected``）
- ``DOCS[kind][阶段标签]`` 是比对用的单据快照：阶段标签 = 精确阶段名；有子状态谓词的格按谓词取值展开，写成「阶段·子状态」
- persona：pr（本人，即快照的 owner_id / negotiator_id）、pr2（另一个 PR）、pr_manager、admin、finance、operations、warehouse，
  按 ``DEFAULT_ROLES`` 构造
- 行类型：``action`` / ``field``（同名键如 promotion 的 ``metrics`` 两种都有）

PR-1 两张表都为空；PR-2 起每个 PR 把它碰到的行加进来（设计 10.1）。
"""

from __future__ import annotations

from typing import Any

EXPECTED: dict[str, dict[tuple[str, str, str, str], str]] = {}

DOCS: dict[str, dict[str, Any]] = {}
