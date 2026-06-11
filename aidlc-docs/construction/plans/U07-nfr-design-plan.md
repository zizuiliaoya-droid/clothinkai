# U07 NFR 设计计划（NFR Design Plan）

> 单元：U07 — 企微集成基础
> 范围：U07 增量设计模式 P-U07-01~05（凭据加密落地 / WecomClient+token 缓存 / 扫描编排 / 群发执行+频控降级 / 回调签名+幂等）；其余继承 U01-U06
> 节奏：NFR Design 阶段 = 本计划 + 2 文档（nfr-design-patterns.md + logical-components.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — WecomClient 同步还是异步
- [Answer] **异步**（httpx.AsyncClient + await cache）。与 U06a runner 一致：Celery 任务入口 `asyncio.run(_async_impl())`，内部全异步；HTTP 端点（配置/绑定/test）本就 async。统一异步避免 sync/async 混用。

### Q2 — 扫描逐租户上下文
- [Answer] 复用 U06a NF-1：`scan_and_dispatch_urge` 用 bypass session 读「启用 wecom_config 的租户列表」，再对每租户用 AsyncSessionApp + `SELECT set_config('app.tenant_id', :tid, true)`（SET LOCAL 等价）查候选 + 建 message；`system_context()` 包裹供 audit。

### Q3 — 每消息事务边界
- [Answer] `execute_wecom_message(message_id)` 一个 message 一个 task，独立 AsyncSessionApp 事务 + per-task SET LOCAL；频控/发送/状态更新同事务；失败不影响其他 message（同 U06a 行级隔离理念）。

### Q4 — 频控当天范围计算
- [Answer] 当天 = `created_at >= 当天0点(Asia/Shanghai)` 转 UTC 比较（复用 U04 `get_today` + Asia/Shanghai）；计数 status ∈ {created, sent}（已实际发起的）。

### Q5 — 回调 tenant 路由
- [Answer] 回调 URL 形如 `/api/wecom/callback/{tenant_id}`（配置回调时带租户路径），bypass 查该租户 wecom_config 取 token/aes_key 校验；避免按 corp_id 全表扫描。signature 失败 403 + audit。

### Q6 — NotificationService 归属
- [Answer] 放 `modules/wecom/notification_service.py`（MVP 首个消费者在 wecom）；notification 表 + NotificationRepository 同模块；API `modules/wecom/notification_api.py`。V1 设计模块复用同 service。

### Q7 — 模板渲染位置
- [Answer] 纯函数 `modules/wecom/domain.py::render_template(content, ctx)`（替换 4 白名单变量，缺值空串）；扫描时渲染并存 `rendered_content`（快照），执行时直接用快照（模板后续编辑不影响在途消息）。

---

## 2. 执行步骤

- [x] 2.1 `U07/nfr-design/nfr-design-patterns.md`：P-U07-01 凭据加密落地（AESGCM+HKDF+audit）/ P-U07-02 WecomClient 异步+token 缓存+失效重试 / P-U07-03 扫描编排（逐租户+聚合+幂等）/ P-U07-04 群发执行+频控降级（每消息事务+DB 计数+notification）/ P-U07-05 回调签名+幂等状态推进 + 一致性校验
- [x] 2.2 `U07/nfr-design/logical-components.md`：组件清单（models/repository/service/client/domain/tasks/api/schemas/deps/notification + crypto 改动 + metrics + config + celery beat + migration 011）+ 依赖图 + 注册序列
- [x] 2.3 诊断器无警告 + 与 functional-design / nfr-requirements 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 设计文档。**
