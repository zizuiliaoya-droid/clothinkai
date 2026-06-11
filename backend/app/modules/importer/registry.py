"""U06a ImportAdapterRegistry（进程内注册中心，P-U06a-02）。

注册时机（与 U05 listener 同模式，双进程加载，NF-4）：
- HTTP 进程：main.py lifespan `register_import_adapters()`
- worker 进程：celery_app `worker_process_init` 信号调用同一函数

数据结构：类级 dict。注册在启动期单线程完成，运行期只读，无需锁。
source 有效集合 = 已注册 adapter 的 source 键（upload 时白名单校验）。
"""

from __future__ import annotations

import logging

from app.modules.importer.adapter import ImportAdapter

log = logging.getLogger(__name__)


class ImportAdapterRegistry:
    """导入适配器注册中心。"""

    _adapters: dict[str, ImportAdapter] = {}

    @classmethod
    def register(cls, adapter: ImportAdapter) -> None:
        """注册一个 adapter（按 adapter.source 为键）。重复注册覆盖（启动期幂等）。"""
        cls._adapters[adapter.source] = adapter
        log.info("import_adapter_registered", extra={"source": adapter.source})

    @classmethod
    def get(cls, source: str) -> ImportAdapter | None:
        """取 adapter（runner 内按 batch.source 取；缺失 → None）。"""
        return cls._adapters.get(source)

    @classmethod
    def sources(cls) -> frozenset[str]:
        """已注册 source 白名单（upload 校验用）。"""
        return frozenset(cls._adapters.keys())

    @classmethod
    def clear(cls) -> None:
        """清空（测试用）。"""
        cls._adapters.clear()


__all__ = ["ImportAdapterRegistry"]
