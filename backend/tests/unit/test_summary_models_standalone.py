"""汇总表模型要能在「只 import 刷新链路」的进程里完成持久化。

这条测试是为一个**真实上线故障**立的：生产第一次跑刷新任务时抛

    NoReferencedTableError: Foreign key associated with column
    'product_roi_summary.goods_main_id' could not find table 'goods_main'

原因是 FK 指向的表没在同一份 metadata 里注册。HTTP 路径碰不到 —— app 启动时
router 链式 import 了全部 models；但 Celery worker 的 import 链只有
``celery_app → summary_tasks → summary_refresh_service → summary_models``，
到不了 product / auth 的 models。

两个让这个 bug 特别容易漏掉的地方：

1. **本仓库的测试套件碰不到它**：``tests/conftest.py`` 顶部把所有 models 都 import
   了，metadata 永远完整，缺失在测试里完全隐形。所以这里起**干净的子进程**，
   只走 Celery 那条 import 链。
2. **``configure_mappers()`` 检查不出来**。FK 的目标表是懒解析的，
   ``configure_mappers()`` 照样返回成功；生产那条栈是在 flush 的表排序
   （``mapper._sort_tables`` → ``fk.column``）里才炸。第一版探针就是用
   ``configure_mappers()`` 写的，注释掉 import 之后依然绿 —— 测了个没用的东西。
   所以这里直接查根因：**每个 FK 的目标表必须在 metadata 里**，
   再额外编译一次 INSERT 复现真实触发点。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# 刻意不 import 任何 app 模块：本模块一旦碰到 app，conftest 的全量 import 就生效，
# 子进程之外的断言会变得没有意义。

_SUMMARY_TABLES = (
    "product_roi_summary",
    "pr_work_progress_summary",
    "shop_daily_summary",
    "shop_week_summary",
    "shop_month_summary",
)

_PROBE_TEMPLATE = textwrap.dedent(
    """
    # 模拟 Celery worker 的 import 链，不碰 app.main / router
    {entry}
    from sqlalchemy import insert
    from sqlalchemy.orm import configure_mappers
    from sqlalchemy.sql.ddl import sort_tables_and_constraints

    from app.core.db import Base
    from app.modules.report.summary_models import (
        PrWorkProgressSummary,
        ProductRoiSummary,
        ShopDailySummary,
        ShopMonthSummary,
        ShopWeekSummary,
    )

    configure_mappers()

    tables = {tables!r}
    missing = []
    for name in tables:
        t = Base.metadata.tables[name]
        for fk in t.foreign_keys:
            # fk.target_fullname 是声明时写的字符串，解析它才会去找真表
            target = fk.target_fullname.split(".")[0]
            if target not in Base.metadata.tables:
                missing.append(f"{{name}}.{{fk.parent.name}} -> {{target}}")
    if missing:
        raise SystemExit("MISSING_FK_TARGETS: " + "; ".join(missing))

    # 真实触发点：flush 时要给相关表排序，那一步会解析 fk.column
    models = [
        ProductRoiSummary,
        PrWorkProgressSummary,
        ShopDailySummary,
        ShopWeekSummary,
        ShopMonthSummary,
    ]
    sort_tables_and_constraints([m.__table__ for m in models])
    for m in models:
        str(insert(m).compile())

    print("PERSIST_READY")
    """
)


def _probe(entry: str) -> subprocess.CompletedProcess[str]:
    code = _PROBE_TEMPLATE.format(entry=entry, tables=_SUMMARY_TABLES)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
    )


class TestImportChainIsSelfSufficient:
    def test_refresh_service_chain(self) -> None:
        """只 import 刷新服务，FK 目标表就要都在 metadata 里。"""
        proc = _probe("from app.modules.report import summary_refresh_service  # noqa: F401")
        assert "PERSIST_READY" in proc.stdout, (
            f"刷新服务的 import 链不自洽\n" f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
        )

    def test_celery_task_chain(self) -> None:
        """Celery 任务模块的 import 链 —— 这是生产实际走的路径。"""
        proc = _probe("from app.tasks import summary_tasks  # noqa: F401")
        assert "PERSIST_READY" in proc.stdout, (
            f"Celery 任务 import 链不自洽（生产就是在这里炸的）\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
        )

    def test_models_alone(self) -> None:
        """连 service 都不经过，只 import models 本身也要自洽。

        这样以后谁直接拿 summary_models 去写脚本也不会踩同一个坑。
        """
        proc = _probe("import app.modules.report.summary_models  # noqa: F401")
        assert "PERSIST_READY" in proc.stdout, (
            f"models 自身的 import 链不自洽\n" f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
        )
