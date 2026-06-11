# U18 基础设施设计（AI 决策建议）

> 增量式：复用 U01 全部基础设施（Zeabur 6 服务 + RLS + Sentry + Secrets）+ U07 外部出站模式。
> 单元：EP11-S01、S02、S03（P3 项目收官单元）。唯一增量 = migration 022（1 表）+ DEEPSEEK_* 配置。

---

## 1. 服务拓扑（无变更）

| 服务 | U18 用途 | 变更 |
|---|---|---|
| backend | AI API（同步 + DeepSeek 出站） | 无（挂 ai_router） |
| celery-worker | — | 无（ai_advisory_request 占位不启用） |
| celery-beat | — | 无 |
| postgres | ai_advice_log 1 表 | migration 022 |
| redis | — | 无（P3 无缓存） |
| frontend | （不在本单元范围） | 无 |

**结论**：无新服务、无新进程、无 Celery/Beat、无资源规格变更。

---

## 2. 数据库变更（migration 022）

### 表：ai_advice_log
| 列 | 类型 | 约束 |
|---|---|---|
| base_cols | TenantScopedModel + FK tenant RESTRICT | RLS |
| advice_type | String(16) | NOT NULL |
| request_payload | JSONB | NOT NULL DEFAULT '{}' |
| response_text | Text | NULL |
| confidence | String(8) | NULL |
| status | String(8) | NOT NULL |
| model | String(32) | NULL |
| latency_ms | Integer | NULL |
| created_by | UUID FK user SET NULL | NULL |

索引：`idx_ai_advice_log_tenant_type` (tenant_id, advice_type, created_at)。RLS 启用。

### scope seed
- permission：ai.advice:read（ON CONFLICT(scope) DO NOTHING）。
- role_permission：pr / pr_manager / operations 绑 ai.advice:read（admin 通配 "*" 已覆盖）。

### 迁移属性
- revision `"022_u18_ai_advice_log"`（20 字符 ≤ 32），down_revision `"021_u17_bundle_bi_export"`。
- 无回填；down 安全 drop 表 + 删 scope。

---

## 3. 复用基础设施（零新依赖）

| 维度 | 复用 | 说明 |
|---|---|---|
| 依赖 | httpx + SQLAlchemy + prometheus | U07/U01 已有 |
| 环境变量 | **新增 DEEPSEEK_*（4 个）** | API_BASE/API_KEY/MODEL/TIMEOUT |
| R2 / Redis | 无用量 | P3 无缓存 |
| Sentry | 非降级类异常 capture | U01 |
| 数据 | U14 report / U15 wecom_alert_log / U03 blogger | 复用 service/表 |

---

## 4. DEEPSEEK 配置（环境变量）

| 变量 | 默认 | 敏感 | 说明 |
|---|---|---|---|
| DEEPSEEK_API_BASE | https://api.deepseek.com | 否 | API 域名 |
| DEEPSEEK_API_KEY | （空） | **是（Zeabur Secrets）** | 空 → 全 503 降级 |
| DEEPSEEK_MODEL | deepseek-chat | 否 | 模型名 |
| DEEPSEEK_TIMEOUT | 30 | 否 | 超时秒数 |

- API_KEY 存 Zeabur Secrets，不入仓库/日志；缺失视为不可用（503 降级）。

---

## 5. 外部出站（部署面）

- 出站到 api.deepseek.com（HTTPS /chat/completions）；backend 需可访问外网（Zeabur 香港节点可访问）。
- 未配置 API_KEY → 不出站，直接 503 降级。
- 超时 30s（DEEPSEEK_TIMEOUT），不长时间挂起。

---

## 6. 部署一致性

- U18 依赖 U14（report）/U15（wecom_alert_log）/U03（blogger）/U02（product）均已部署。
- migration 顺序：022 紧接 021（U17），head 推进到 022。
- AI 模块独立：DeepSeek 不可用 / 未配置时全 503 降级，不影响其余模块与 main 启动。

---

## 7. 本地验证

- Docker PG16:5561 + Redis7:6416 + python:3.12-slim（U18 唯一端口）。
- alembic upgrade head（含 022）；U18 子集（test_ai_advisory + test_ai_advice + test_ai_api）+ 全量回归；覆盖率 ≥70%。
- DeepSeek 全程 monkeypatch（不调真实 API；不配 DEEPSEEK_API_KEY → 测降级路径）。

---

## 8. 回滚

- 代码：移除 ai_router 挂载。
- DB：migration 022 down（drop ai_advice_log + 删 ai.advice:read scope）；无外键被引用，安全幂等。
- 配置：移除 DEEPSEEK_* env（或留空 → 全 503 降级，不报错）。

---

> spec-format 校验「Missing ## Overview / ## Architecture」为已知假阳性，IGNORE。
