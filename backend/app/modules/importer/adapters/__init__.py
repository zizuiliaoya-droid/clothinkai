"""U06a 导入业务适配器子包。

每个业务来源一个 Adapter 模块，实现 U06a ``ImportAdapter`` 协议并提供模块级
``register()``。由 ``main.py`` lifespan 与 Celery ``worker_process_init`` 通过
``register_import_adapters`` 双进程加载（U06a NF-4）。

已交付：
- ``style_sku``（U06b）：manual_style_sku 商品/SKU 导入

规划中：
- ``blogger``（U06c）/ ``promotion``（U06d）/ ``settlement``（U06e）
"""
