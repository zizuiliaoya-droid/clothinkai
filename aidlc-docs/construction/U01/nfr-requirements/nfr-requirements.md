# U01 NFR 需求（NFR Requirements）

> 把需求文档第 3 节的高层 NFR 落地到 U01 单元的具体可量化指标。

---

## 1. 性能 NFR

### 1.1 响应时间
| 指标 | 阈值 | 测量方式 |
|---|---|---|
| `POST /api/auth/login` P95 | ≤ 300ms（含 bcrypt 验证） | Prometheus histogram |
| `POST /api/auth/refresh` P95 | ≤ 100ms | 同上 |
| `PUT /api/auth/password` P95 | ≤ 400ms | 同上 |
| 用户管理类 API（CRUD）P95 | ≤ 200ms | 同上 |
| 审计日志查询 P95 | ≤ 500ms（带索引） | 同上 |
| 全局 API P95 整体 | ≤ 500ms（需求 3.1） | 同上 |

> 说明：登录因 bcrypt cost=12 自然慢，单独放宽到 300ms。其他认证类操作 ≤100-200ms 留出余量。

### 1.2 吞吐量
- 单 backend 实例：≥ 50 RPS（Zeabur 单容器，4 核估算）
- 并发用户 ≥ 50（与需求 3.1 一致）
- bcrypt cost=12 单 verify ≈ 100ms，单实例理论并发上限 ≈ 40 并发登录/秒，足够 50 用户登录峰值

### 1.3 数据库连接池
- `pool_size=5`，`max_overflow=10`（Q5=C 保守）
- 最大 15 个连接（与 Zeabur PostgreSQL 默认配额匹配）
- `pool_recycle=3600` 秒，`pool_pre_ping=True`
- 监控：连接池使用率 > 80% 触发预警

### 1.4 Redis 性能
- 单租户单用户权限缓存 ≤ 1KB
- 限流计数 / 黑名单 entry ≤ 100 字节
- 总内存预算（U01）：< 50MB（Redis 7 默认 100MB 足够）

---

## 2. 可用性 NFR

### 2.1 总体目标
- 可用性 ≥ 99.5%（需求 3.2）
- RPO ≤ 24 小时
- RTO ≤ 4 小时
- 每季度恢复演练

### 2.2 单点故障识别
| 组件 | 单点风险 | 缓解 |
|---|---|---|
| backend | Zeabur 容器重启可恢复 | 状态无关，多实例由 Zeabur 自动调度 |
| celery-worker | 任务幂等 | 失败任务重试，audit_log 不会重复写（jti 唯一） |
| celery-beat | 单实例运行 | 失败重启 < 1 分钟，每天 03:00 备份允许 1-2 小时延迟 |
| postgres | 单实例 | Zeabur 插件层备份；本系统每日 pg_dump 到 R2 |
| redis | 缓存丢失可重建 | 失效时回退到 DB 查询，不阻断业务 |

### 2.3 数据持久性
- audit_log：append-only，DB REVOKE UPDATE/DELETE 强制
- 每日 03:00 pg_dump 到 R2 backups/（保留 30d 日 + 1y 月）
- backup_record 跟踪每次备份的 SHA256

---

## 3. 安全 NFR

### 3.1 认证
- JWT 算法：HS256（HMAC-SHA256），密钥 ≥ 256 位从环境变量读取
- access_token 30 分钟；refresh_token 7 天
- bcrypt cost factor = 12（passlib[bcrypt] 默认）
- 密码强度：≥10 字符 + 大小写 + 数字（详见 business-rules BR-PWD-001）

### 3.2 授权
- 模块/功能级 RBAC：U01 范围内强制
- 字段级权限：U09 启用，U01 不强制
- 多租户 ORM 注入 + RLS 兜底

### 3.3 凭据保护
- 加密密钥（JWT secret / AES master key）只通过环境变量注入
- 不在 audit_log / 日志 / API 响应中出现明文
- 临时密码（首次创建）一次性返回，不写日志

### 3.4 网络
- HTTPS：Zeabur 自动证书
- CORS：仅允许 `https://app.clothinkai.com`（生产）+ `http://localhost:5173`（开发）
- API 限流（**修订**：分层实施，避免 slowapi key_func 中读 body 的实现陷阱）：
  - **L1 全局**：所有 API 100 req/min/IP（slowapi）
  - **L2 登录端点**：20 req/min/IP（slowapi @limiter.limit("20/minute")）
  - **L3 (IP, username)**：5 次/15 分钟（AuthService 内 Redis incr，详见 nfr-design-patterns.md 第 3 节）
  - **L4 账户累计**：10 次失败 → 锁账户（AuthService 内 DB user.failed_login_count，需管理员解锁）

### 3.5 审计
- audit_log append-only（DB REVOKE）
- 每日记录：登录成功 + 登录失败 + 用户管理操作 + 角色/权限变更 + 平台管理员跨租户访问
- 1 年内查询，超期归档到 R2

### 3.6 输入校验
- 所有 API 请求 Pydantic v2 严格校验
- 字段长度限制（防 OOM 攻击）：username ≤ 64 / password ≤ 128 / display_name ≤ 64
- 用户名仅允许 `[a-zA-Z0-9_\-\.]`，避免 SQL/命令注入

---

## 4. 多租户 NFR

### 4.1 隔离强度
- ORM Session 注入（应用层防漏）
- PostgreSQL RLS（数据库层兜底）
- **本地开发也启用 RLS**（Q14=B），与生产一致；调试时切换到 BYPASS 角色

### 4.2 测试覆盖
- 典型实体（user/style/promotion/settlement）必须有租户隔离集成测试（Q13=B）
- 其他实体通过基类 `TenantScopedModel` 单元测试覆盖
- 每个 MVP 单元末尾跑一次跨租户回归

### 4.3 跨租户场景
- system_context()：仅限 Celery 系统任务（备份、清理过期 token）
- platform_admin token：每次跨租户访问写 audit_log
- Worker pull API：通过专用 worker_token，不绕过租户隔离（Worker 拿到的任务和凭据自带 tenant_id）

---

## 5. 可维护性 NFR

### 5.1 代码质量
- 类型注解：100%（强制 mypy/pyright 在 CI 中跑）
- 代码风格：black + ruff（line length 100）
- 提交前钩子：pre-commit（black / ruff / mypy）

### 5.2 数据库迁移
- Alembic：每个 schema 变更一个 migration
- 必须可回滚（downgrade 可执行）
- migration 命名：`XXXX_<unit>_<change>.py`，如 `001_u01_seed_initial_data.py`

### 5.3 日志
- structlog 输出 JSON
- 字段：`timestamp`, `level`, `module`, `tenant_id`, `user_id`, `request_id`, `event`, `**context`
- 敏感字段过滤：password / token / secret 自动 redact

### 5.4 文档
- API 文档：FastAPI 自动 OpenAPI（/api/docs）
- README：包含本地启动 + 环境变量说明
- 关键模块：docstring 描述意图

---

## 6. 可观测性 NFR

### 6.1 指标
- `prometheus-fastapi-instrumentator` 自动暴露 `/metrics`：
  - `http_requests_total{method, path, status}`
  - `http_request_duration_seconds{method, path}`
  - `http_requests_in_progress`
- 自定义业务指标（U01 起）：
  - `auth_login_total{result="success|failed|locked"}`
  - `auth_token_refresh_total`
  - `audit_log_inserts_total`
  - `db_pool_in_use`
  - `backup_status{type="daily|monthly"}`

### 6.2 异常追踪
- Sentry：免费层 5K 事件/月足够 U01
- 集成 FastAPI middleware
- 不向 Sentry 发送 PII（password / token）

### 6.3 健康检查
- `GET /health`：返回 200 + DB 连通性 + Redis 连通性
- Zeabur 用此端点做容器健康判断

---

## 7. 测试 NFR

### 7.1 测试框架
- pytest + pytest-asyncio + pytest-cov + httpx (TestClient)（Q11=A）

### 7.2 测试数据库
- 共享 PostgreSQL 实例，每个测试事务 + 回滚（Q12=B）
- 通过 `pytest-postgresql` 插件管理

### 7.3 覆盖率目标
| 类别 | 覆盖率 |
|---|---|
| Domain 层 | ≥ 90% |
| Service 层 | ≥ 80% |
| Repository 层 | ≥ 70%（其余靠集成测试） |
| Router 层 | ≥ 60%（其余靠 API 集成测试） |
| 整体 | ≥ 80% |

### 7.4 必跑测试种类
- 单元测试：Domain 状态机 / 权限计算算法 / urge_status 计算
- 集成测试：所有 API 端点的正常 + 异常路径
- 多租户测试：典型实体的租户隔离（Q13=B）
- 安全测试：SQL 注入 / XSS / CSRF / 越权访问

### 7.5 RLS 测试
- 单独测试套件 `tests/integration/test_rls.py`：使用真实 PostgreSQL，验证 RLS 策略生效
- 默认 CI 跑（Q14=B 本地启用）

---

## 8. 备份与恢复 NFR

### 8.1 工具链
- Q15=A：Celery 任务内通过 `subprocess.run(['pg_dump', ...])` 调用
- backend / celery-worker Docker 镜像必须安装 `postgresql-client-16`
- 备份文件 gzip 压缩 + SHA256 校验

### 8.2 备份频率与保留
- 每日 03:00：daily 备份，保留 30 天
- 每月 1 日的 daily 自动升级为 monthly，保留 1 年
- 清理任务每天 04:00 跑

### 8.3 恢复演练
- 半自动脚本 `backend/scripts/restore_backup.py`
- 每季度演练 1 次，写 backup_record(type=restore_drill)

---

## 9. NFR 与故事的映射

| 故事 / NFR | 映射的 NFR 类别 |
|---|---|
| EP01-S01 用户登录 | 性能（1.1 登录 ≤300ms）+ 安全（3.1 JWT/bcrypt + 3.4 限流）+ 可观测（6.1 auth_login_total） |
| EP01-S02 修改密码 | 安全（3.1 密码强度）+ 测试（7.4 必须跑 token 失效场景） |
| EP01-S03 用户管理 | 安全（3.5 audit_log 必记）+ 可观测（6.1） |
| EP01-S04 角色分配 | 安全（3.2 RBAC）+ 测试（7.4 角色变更触发 token 失效） |
| EP01-S07 多租户隔离 | 多租户（4 全部条目）+ 测试（7.4 RLS + 跨租户回归） |
| EP01-S08 审计日志 | 安全（3.5）+ 可维护（5.3 结构化日志） |
| EP10-NFR03 多租户 | 多租户（4） |
| EP10-NFR04 备份 | 备份（8） |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 与需求文档第 3 节高层 NFR 一致 | ✅ |
| 与 functional-design business-rules 一致 | ✅ |
| 15 个决策问题全部转化为可量化指标 | ✅ |
| 每个 EP01 故事都有 NFR 映射 | ✅ |
| 每个 NFR 都有可测量手段 | ✅ |
