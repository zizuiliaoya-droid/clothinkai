"""U06a 统一导入框架模块。

框架层（不含具体业务 Adapter — 那些在 U06b/c/d/e）：
- 通用上传 API + import_batch / import_job / field_mapping ORM
- file_hash 去重 + 异步解析编排（run_import_batch Celery 任务）
- ImportAdapter 协议 + ImportAdapterRegistry（注册中心）
- 失败明细下载 + 两类失败重试

关键设计（详见 nfr-design/nfr-design-patterns.md P-U06a-01~05）：
- NF-1：runner per-row 事务内 SET LOCAL app.tenant_id（防连接池串租）
- NF-2：upload DB 先行 + UNIQUE 原子去重 + R2 失败补偿
- NF-3：retry 原子 processing claim（批次互斥）
- NF-4：celery autodiscover import_tasks + worker_process_init 注册 Adapter
- NF-5：importer.batch:read/write + importer.mapping:write 权限
- FB-A：用 U01 R2 helper（不依赖 U05 Attachment ORM）
"""
