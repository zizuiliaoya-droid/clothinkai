"""流程线横切模块（流程线设计 5.4 / 6 / 7.8）。

``matrix.py``（能力矩阵：一张表、三个出口；各单据的表由单据模块登记）、``actor.py``（从库里组装当前用户）
与 ``exceptions.py``；时间线（``line_events.py``、``/api/lines``）随 PR-3 / PR-4 加进来。
"""

from __future__ import annotations
