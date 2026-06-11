# U01 NFR 需求计划（NFR Requirements Plan）

> 单元：U01 — 认证 + 多租户基础 + 备份框架  
> 范围：识别 U01 单元相关的非功能需求，做技术栈选型决策

---

## 概述

需求文档第 3 节已经定义了高层 NFR：
- 性能：API P95 ≤500ms，并发 ≥50
- 可用性 ≥99.5%、RPO 24h、RTO 4h
- 安全：JWT/bcrypt/RBAC/HTTPS/限流/CORS/AES-256
- 多租户：共享 DB + tenant_id + RLS

应用设计已锁定 FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 / Redis 7 / Celery / Cloudflare R2 / Zeabur。

本计划聚焦 U01 范围内**尚未决定的具体技术选型和量化阈值**。

---

## 第一部分：决策问题

### Question 1 — JWT 库
Python JWT 库选择？

A) **PyJWT**（最流行，社区广，文档齐全）
B) **python-jose**（功能丰富，支持 JWE 等）
C) **authlib**（综合 OAuth/OIDC，对企微有用）
D) Other

[Answer]: A

### Question 2 — 密码哈希库
bcrypt 通过哪个 Python 库使用？

A) **passlib[bcrypt]**（高级封装，支持算法升级）
B) **bcrypt**（原生 binding，更轻量）
C) Other

[Answer]: A

### Question 3 — API 限流方案
应用级限流实施？

A) **slowapi**（FastAPI 友好的 limits 包装，需求文档已提及）
B) **fastapi-limiter**（基于 Redis）
C) **自研中间件**（基于 Redis incr）
D) Other

[Answer]: A

### Question 4 — Redis 客户端
Python Redis 客户端？

A) **redis-py 5.x（异步）**：`redis.asyncio.Redis`，主流选择
B) **aioredis**（已合并到 redis-py）
C) Other

[Answer]: A

### Question 5 — 数据库连接池
SQLAlchemy 异步连接池配置？

A) **pool_size=10, max_overflow=20**（中等并发，适合 50 人在线）
B) **pool_size=20, max_overflow=40**（宽松，适合突发流量）
C) **pool_size=5, max_overflow=10**（保守，按 Zeabur 默认 PostgreSQL 配额限制）
D) Other（请说明）

[Answer]: C

### Question 6 — Redis 缓存 TTL 策略
权限缓存 / 限流计数 / refresh_token 黑名单的 TTL？

A) **权限缓存 10 分钟 + 限流 15 分钟 + refresh_token 黑名单 = 7 天**（与 token 同步）
B) **权限缓存 5 分钟 + 限流 15 分钟 + 黑名单 7 天**（更保守的权限）
C) **权限缓存 30 分钟 + 限流 15 分钟 + 黑名单 7 天**（更激进）
D) Other

[Answer]: B

### Question 7 — refresh_token 清理频率
过期 refresh_token 的清理任务？

A) **每周一次**（每周日 04:00），删除 expires_at < NOW() 的记录
B) **每天一次**（每天 04:00 备份后）
C) **每次签发新 refresh_token 时顺便清理该用户的过期记录**（懒清理）
D) Other

[Answer]: B

### Question 8 — 结构化日志库
Python 日志框架？

A) **structlog**（高度结构化，性能好，社区主流）
B) **logging（标准库）+ python-json-logger**（最简）
C) **loguru**（开发友好，但生产场景较少用）
D) Other

[Answer]: A

### Question 9 — 监控指标
应用级监控（响应时间/错误率/连接数）？

A) **Prometheus + prometheus-fastapi-instrumentator**（行业标准，Zeabur 支持）
B) **OpenTelemetry**（更通用，但部署复杂）
C) **仅 Zeabur 自带监控 + 自定义业务日志**（最轻量）
D) Other

[Answer]: A

### Question 10 — 异常追踪
异常聚合与告警？

A) **Sentry**（行业标准，免费层够用）
B) **仅日志 + 企微告警**（U07 完成后）
C) **本阶段不实施**，留到 V1（U15 监控告警）
D) Other

[Answer]: A

### Question 11 — 测试框架
后端测试框架与覆盖率工具？

A) **pytest + pytest-asyncio + pytest-cov + httpx (TestClient)**
B) **unittest（标准库）+ coverage**
C) Other

[Answer]: A

### Question 12 — 测试数据库
集成测试用什么 DB？

A) **每个测试 session 起一个临时 PostgreSQL**（testcontainers-python）
B) **共享一个测试 DB，每个测试事务回滚**
C) **SQLite 内存模拟**（最快但无法测 RLS / JSONB / 高级特性）
D) Other

[Answer]: B

### Question 13 — 多租户测试覆盖率要求
多租户隔离测试的覆盖范围？

A) **每个 Repository 都有"租户 A 不可见租户 B 数据"的集成测试**
B) **典型实体（user/style/promotion/settlement）有租户隔离测试，其他靠基类继承**
C) **基类 TenantScopedModel 单测 + 应用级"金丝雀"测试（生产抽样）**
D) Other

[Answer]: B

### Question 14 — RLS 关闭/启用的开发体验
开发者本地调试时如何处理 RLS？

A) **本地 Docker PostgreSQL 不启用 RLS**（开发简单），生产/staging 启用 + 定期回归
B) **本地始终启用 RLS**（与生产一致），通过 BYPASS 角色调试
C) **测试时强制启用 RLS**，开发时关闭
D) Other

[Answer]: B

### Question 15 — 备份脚本工具链
pg_dump 脚本运行环境？

A) **Celery 任务内通过 subprocess 调用 pg_dump**（pg_dump 二进制需在容器中安装）
B) **专用 backup 容器/Job**（独立部署的备份服务）
C) **Zeabur 的 PostgreSQL 插件自带备份机制**（如有）
D) Other

[Answer]: A
