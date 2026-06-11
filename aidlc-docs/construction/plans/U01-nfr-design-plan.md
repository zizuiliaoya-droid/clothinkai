# U01 NFR 设计计划（NFR Design Plan）

> 单元：U01 — 认证 + 多租户基础 + 备份框架  
> 范围：把 NFR Requirements 转化为具体设计模式和逻辑组件

---

## 概述

NFR Requirements 已经定下了量化指标（P95 / pool_size / TTL）和技术栈（PyJWT/passlib/structlog/Sentry/Prometheus）。  
应用设计已经定下了模式（Pydantic 动态 Schema + 装饰器、StateMachine 基类、@audit 装饰器）。

本阶段聚焦把这些落到**可实施的设计模式细节**：
- 中间件链顺序与上下文注入
- RLS 角色切换协议
- 双层限流的协作
- 健康检查与就绪检查
- Sentry 多租户上下文标记

---

## 第一部分：决策问题

### Question 1 — 中间件执行顺序
FastAPI 中间件的注册顺序会影响 tenant_id 注入与限流时机。建议顺序？

A) **CORS → SentryAsgi → RequestId → Limiter → Tenancy → Auth Dep → Router**（推荐）  
   - CORS 最外层
   - SentryAsgi 抓未捕获异常
   - RequestId 给每请求分配 ID（structlog 用）
   - Limiter 在 tenancy 之前（IP 限流不依赖租户）
   - Tenancy 在 Auth Depends 解析 JWT 之后才有 tenant_id（在依赖里设）

B) **CORS → Limiter → SentryAsgi → RequestId → Tenancy → Router**（限流前置）

C) Other

[Answer]: A

### Question 2 — tenant_id 注入位置
JWT 解析得到 tenant_id 后，怎么注入到 Session？

A) **依赖注入链**：`get_session()` 依赖于 `get_current_user()` 依赖于 JWT 解析。在 `get_session()` 内部调用 `set_tenant_context(session, user.tenant_id)`，并执行 `SET LOCAL app.tenant_id`
B) **ASGI middleware 直接读 JWT**：在 middleware 中解析 JWT（不走依赖链）写入 contextvars
C) **混合**：Middleware 解析 JWT 写 contextvars；Session 依赖在创建时读 contextvars 调用 SET LOCAL
D) Other

[Answer]: C

### Question 3 — RLS 角色切换
应用如何在 `clothing_app`（启用 RLS）和 `clothing_bypass`（绕过 RLS）之间切换？

A) **两个 SQLAlchemy 引擎**：`engine_app`（默认连 clothing_app 用户）+ `engine_bypass`（连 clothing_bypass）。系统任务/平台管理员用 engine_bypass，业务请求用 engine_app
B) **同一引擎，不同 SET LOCAL**：默认用 clothing_app 连接；platform_admin 或 system_context 时执行 `SET LOCAL app.bypass_rls = 'on'`，由 RLS policy 内的 OR 条件放行
C) **数据库角色切换**：`SET ROLE clothing_bypass` 在事务内
D) Other

[Answer]: A

### Question 4 — 双层限流的实现协作
IP 维度（slowapi）+ 账户维度（user.failed_login_count）怎么协作？

A) **slowapi 处理 IP 限流（5/15min）**返回 429；如果通过，进入 Service 层做账户级累计（10 次锁），返回 423
B) **完全在 Service 层手写**（不用 slowapi，统一 Redis incr）
C) **slowapi 处理 IP + Service 内显式调用 Redis 检查账户**（细粒度协作，避免双计数）
D) Other

[Answer]: A

### Question 5 — 失败/锁定时的 audit 写入策略
账户被自动锁定（failed_login_count ≥ 10）时是否写 audit？

A) **写一条 user_lock 记录**，actor_type=system，原因 reason="exceeded_login_attempts"
B) **不单独写**，依赖 login_failed 累计记录
C) **写 + 同时给所有管理员推一条站内通知**（U07 完成后含企微通知）
D) Other

[Answer]: A

### Question 6 — 健康检查端点设计
`GET /health` 和 `/ready` 检查内容？

A) **/health**：仅返回 200（liveness，验证进程存活）；**/ready**：检查 DB + Redis 可达
B) **/health 一个端点**：DB + Redis + Celery broker 全检查
C) **/health（liveness）+ /ready（DB+Redis）+ /api/v1/healthcheck（业务级，含简单 DB 查询）**
D) Other

[Answer]: A

### Question 7 — Sentry 多租户上下文
Sentry 异常事件如何标记 tenant_id 和 user_id？

A) **每请求 hook**：在 Tenancy 中间件后用 `sentry_sdk.set_tag("tenant_id", ...)` 和 `set_user(...)`，避免 PII 但保留 user_id
B) **仅记录 user_id**（不带 tenant），减少卡片维度
C) **不主动标记**，靠默认请求上下文
D) Other

[Answer]: A

### Question 8 — structlog 上下文绑定
structlog 怎么自动带 request_id / tenant_id / user_id？

A) **contextvars + structlog.contextvars.merge_contextvars**：中间件在请求开始 bind，结束 reset_contextvars
B) **手动每条日志显式传**（最简但易遗漏）
C) Other

[Answer]: A

### Question 9 — 备份失败的本阶段告警通道
U01 阶段企微未上线（U07 才上），备份失败如何告警？

A) **写 ERROR 级日志 + Sentry 自动捕获**（开发期足够）
B) **写日志 + 触发 backup_record.status=failed**（监控系统轮询发现）
C) **Celery 任务自动重试 2 次后写 Sentry**（避免临时网络抖动误报）
D) Other

[Answer]: C

### Question 10 — Token 失效场景的实施手段
"密码改 / 用户禁用 / 角色变更 / 权限变更 → 触发所有现有 access_token 失效"。建议实施？

A) **Token 校验时比对 user.password_changed_at == token.pwd_iat**：任何上述事件都更新 password_changed_at（即"安全戳"），旧 token 自动失效
B) **黑名单**：所有需要失效的 jti 存 Redis（黑名单 TTL = access_token 剩余有效期）
C) **A + B 双保险**（A 主，B 兜底极端情况）
D) Other

[Answer]: C
