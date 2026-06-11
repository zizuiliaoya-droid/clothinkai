# U04 非功能需求（NFR Requirements）

> 单元：U04 — 推广合作核心  
> 范围：U04 特异性 NFR 增量；通用 NFR 全部继承 U01 + U02 + U03

---

## 1. 与 U01-U03 NFR 基线的关系

### 1.1 完全继承
- 错误码体系 / 认证 / 授权 / 多租户（U01）
- 字段权限硬编码模式（U02 P-U02-02，适配 AMOUNT_VISIBLE_ROLES）
- 审计敏感值脱敏（U02/U03 BR-X-30/31）
- match 降级语义（U02 P-U02-01）
- 监控（Prometheus + Sentry + Loki）

### 1.2 U04 增量
- 容量：MVP 2 万 promotion / 租户（远小于 U02 5 万 style + 50 万 sku，但有 CTE 计算开销）
- **新增**：状态机性能（10 万行下转移操作 P95 ≤ 200ms）
- **新增**：列表 CTE 计算性能（urge_status / dual_platform 实时计算）
- **新增**：本地事件总线 NFR（同事务一致性 + 失败回滚 + V1+ 升级路径）
- **新增**：序列号生成并发安全（行级锁竞争测试）
- **新增**：4 个自定义 Prometheus 指标

---

## 2. 容量需求

### 2.1 数据规模（单租户）

| 表 | MVP 上限 | V1 上限 | V2+ 上限 |
|---|---|---|---|
| `promotion` | 20,000 行 | 100,000 行 | 500,000 行 |
| `promotion_sequence` | 365 行/年 | 365 行/年 | 365 行/年 |

业务文档基线 5494 × 4 倍冗余 = 22000；MVP 上限 2 万足够。

### 2.2 并发负载

| API | 平均 QPS | 峰值 QPS | 触发场景 |
|---|---|---|---|
| `GET /api/promotions/` 列表（含 CTE） | 10 | 50 | PR / PR 主管浏览页面 |
| `GET /api/promotions/{id}` 详情 | 5 | 30 | 详情页打开 |
| `POST /api/promotions/` | 1 | 10 | PR 录入 |
| `PUT /api/promotions/{id}/publish` | 1 | 5 | PR 标记发布 |
| `PUT /api/promotions/{id}/cancel` | 0.5 | 3 | 取消合作 |
| `PUT /api/promotions/{id}/recall/*` | 0.2 | 2 | 召回流程 |
| `POST /api/promotions/{id}/review` | 0.5 | 5 | PR 主管批量审核 |

### 2.3 增长触发器
- 单租户 promotion 突破 5 万行 → P95 监控连续 1 周 > 500ms 触发：
  1. 检查 CTE 索引使用率
  2. 评估添加 stored generated columns（`urge_status` / `is_hit` / `effective_like_count` 落地为字段，触发器维护）
  3. 评估按 cooperation_date 分区（PostgreSQL Declarative Partitioning）
- 突破 50 万行 → V1+ 评估读写分离

---

## 3. 性能需求

### 3.1 SLA 总表

| API | P50 | P95 | P99 | 超时 |
|---|---|---|---|---|
| `GET /api/promotions/` 列表 + CTE | ≤ 80ms | ≤ 300ms | ≤ 600ms | 5s |
| `GET /api/promotions/{id}` 详情 | ≤ 30ms | ≤ 100ms | ≤ 300ms | 3s |
| `POST /api/promotions/`（含序列号锁） | ≤ 100ms | ≤ 300ms | ≤ 800ms | 5s |
| `PUT /api/promotions/{id}/publish`（含状态推进 + 事件） | ≤ 100ms | ≤ 300ms | ≤ 600ms | 5s |
| `PUT /api/promotions/{id}/cancel` | ≤ 80ms | ≤ 200ms | ≤ 500ms | 5s |
| `PUT /api/promotions/{id}/recall/*` | ≤ 80ms | ≤ 200ms | ≤ 500ms | 5s |
| **`POST /api/promotions/{id}/review`（含跨单元事务）** | ≤ 150ms | **≤ 500ms** | ≤ 1s | 5s |
| `update_like_count` 内部 API | ≤ 30ms | ≤ 80ms | ≤ 200ms | 3s |

> 审核 P95 ≤ 500ms 因含跨 U04+U05 同事务（创建 settlement + 事件分发）。

### 3.2 SLA 适用条件
- 测试基准：10,000 promotion + 各角色组合
- 单租户独立测试

### 3.3 监控数据源
- **Prometheus** = SLA 真实数据源（与 U02/U03 一致）
- **Sentry** = 异常 + 慢事务抽样

### 3.4 索引必建项

```sql
-- 业务键唯一
CREATE UNIQUE INDEX uq_promotion_internal_code ON promotion (tenant_id, internal_code);

-- 列表筛选
CREATE INDEX idx_promotion_tenant_active ON promotion (tenant_id, is_active, publish_status);
CREATE INDEX idx_promotion_pr ON promotion (tenant_id, pr_id);
CREATE INDEX idx_promotion_blogger ON promotion (tenant_id, blogger_id, publish_status);
CREATE INDEX idx_promotion_style ON promotion (tenant_id, style_id, publish_status);
CREATE INDEX idx_promotion_cooperation_date ON promotion (tenant_id, cooperation_date DESC);
CREATE INDEX idx_promotion_settlement_status ON promotion (tenant_id, settlement_status);
CREATE INDEX idx_promotion_recall_status ON promotion (tenant_id, recall_status);

-- urge_status CTE 加速
CREATE INDEX idx_promotion_publish_dates ON promotion (tenant_id, publish_status, scheduled_publish_date);

-- 关键字搜索（GIN trgm，复用 U02 已启用扩展）
CREATE INDEX idx_promotion_internal_code_trgm ON promotion
  USING gin (internal_code gin_trgm_ops) WHERE is_active = true;
CREATE INDEX idx_promotion_style_code_snapshot_trgm ON promotion
  USING gin (style_code_snapshot gin_trgm_ops) WHERE is_active = true;
CREATE INDEX idx_promotion_short_name_trgm ON promotion
  USING gin (style_short_name_snapshot gin_trgm_ops) WHERE is_active = true;

-- promotion_sequence 表
CREATE UNIQUE INDEX uq_promotion_sequence ON promotion_sequence (tenant_id, date_key);
```

### 3.5 状态机性能（Q3 决策）
- 转移操作：UPDATE WHERE old_state RETURNING 模式（乐观并发）
- RETURNING 为空 → 409 STATE_TRANSITION_CONFLICT，让用户重试
- 不引入悲观锁，避免热点 promotion 状态推进串行化

### 3.6 序列号生成性能（Q4 决策）
- 行级锁 `SELECT FOR UPDATE` 单行串行化
- 单租户单天 1 QPS 创建率下，锁等待 < 50ms（P95）
- 监控 `promotion_sequence_lock_duration_seconds` Histogram，超 500ms 告警

---

## 4. 安全需求

### 4.1 字段级权限矩阵（U02/U03 模式延续）

#### 4.1.1 角色硬编码可见性矩阵

| 角色 | quote_amount | cost_snapshot |
|---|---|---|
| admin / pr / pr_manager / finance | ✅ | ✅ |
| 其他（merchandiser / designer / operations 等） | ❌ | ❌ |

#### 4.1.2 写权限

| 角色 | quote_amount | cost_snapshot |
|---|---|---|
| admin / pr / pr_manager | ✅ | ✅ |
| finance | ❌（仅读） | ❌ |
| 其他 | ❌ | ❌ |

#### 4.1.3 实施位置
`modules/promotion/legacy_field_permissions.py`：
- `AMOUNT_VISIBLE_ROLES = frozenset({"admin", "pr", "pr_manager", "finance"})`
- `AMOUNT_WRITABLE_ROLES = frozenset({"admin", "pr", "pr_manager"})`

带 `# TODO U09` 注释，与 U02/U03 同模式。

### 4.2 敏感字段不加密（威胁模型）

#### 4.2.1 决策
quote_amount / cost_snapshot 在数据库存明文。

#### 4.2.2 威胁模型边界（与 U02/U03 一致）
- **本决策仅防御**：普通业务用户跨角色越权读取（设计师 / 跟单 / 运营 不应看到金额）
- **本决策不防御**：DBA / 运维（视为可信内部人员）
- **应用层防护**：service 层 BR-U04-51 + Pydantic schema 字段过滤
- **审计**：所有金额变更进 audit_log 但**仅记 `*_changed: true` 标记**

#### 4.2.3 演进选项（V2+）
合规要求时引入 pgcrypto + KMS 集成（独立单元承担）。

### 4.3 自审禁止（业务安全）
- pr_manager 不可审核自己创建的 promotion（pr_id == reviewer.id → 422）
- 防止单人通过审批漏洞自批自己提交的高额合作

### 4.4 输入验证

| 字段 | 长度限制 | 字符集 | 校验位置 |
|---|---|---|---|
| `internal_code` | ≤ 64 | service 自动生成（用户不可手填） | service |
| `style_code_snapshot` | ≤ 64 | 字母 + 数字 + `-` + `_` | 来自 U02 已校验 |
| `publish_url` | ≤ 512 | 合法 URL | Pydantic HttpUrl |
| `like_count` | ≥ 0 | INTEGER | Pydantic + DB CHECK |
| `quote_amount` / `cost_snapshot` | DECIMAL(10,2) ≥ 0 | 数字 | Pydantic + DB CHECK |
| `cancel_reason` / `recall_reason` / `review_reason` | ≤ 2000 | 任意 | Pydantic |

### 4.5 速率限制（继承 U01）
| 维度 | 阈值 |
|---|---|
| IP | 60 req/min |
| 用户 | 600 req/min |
| 写操作 | 30 req/min/用户 |

---

## 5. 监控与可观测性

### 5.1 Prometheus 指标

通用指标自动覆盖。

#### 5.1.1 新增 4 个自定义指标

```python
# core/metrics.py（追加）

promotion_state_transitions_total: Counter = Counter(
    "promotion_state_transitions_total",
    "Total promotion state machine transitions",
    labelnames=("from_state", "to_state", "status_field"),  # status_field ∈ publish/recall/settlement
)

settlement_requested_events_total: Counter = Counter(
    "settlement_requested_events_total",
    "Total SettlementRequested events dispatched",
    labelnames=("result",),  # dispatched / handler_failed / no_handler
)

promotion_sequence_lock_duration_seconds: Histogram = Histogram(
    "promotion_sequence_lock_duration_seconds",
    "Duration of SELECT FOR UPDATE on promotion_sequence",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

promotion_search_results_count: Histogram = Histogram(
    "promotion_search_results_count",
    "Distribution of promotion list result counts",
    buckets=(0, 1, 10, 100, 1000),
)
```

### 5.2 Sentry
- transaction tag：`module=promotion`
- 复用 `clothing-erp-backend` 项目

### 5.3 日志（与 U02/U03 §5.3 一致策略）
- 关键字段：`tenant_id`, `actor_id`, `request_id`, `module=promotion`, `action=promotion.create/publish/...`
- **不记录敏感字段值**（quote_amount / cost_snapshot），audit_log 仅记 `*_changed: true` 标记

### 5.4 告警阈值

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| `histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/promotions.*"}) > 1` 持续 5min | Prometheus alertmanager → 企微 | SRE |
| `rate(settlement_requested_events_total{result="handler_failed"}[5m]) > 0` | Sentry → 即时 | 后端 leader（关键事件） |
| `histogram_quantile(0.95, promotion_sequence_lock_duration_seconds) > 0.5` | Prometheus alertmanager | SRE |
| `/api/promotions.*` 5xx > 5% 持续 5min | Sentry | 后端 |

---

## 6. 事件总线 NFR（同事务模型）

### 6.1 一致性保证
- **同事务事件总线**：监听器与 publisher 共享同一 SQLAlchemy session 和数据库事务
- 监听器抛异常 → 整个事务回滚（U04 review 不成功，U05 也不创建 settlement）
- DB UNIQUE(promotion_id) 在 settlement 表兜底（U05 端）

### 6.2 失败处理
- 监听器异常自然冒泡到 service 层
- service 层 try/except 包装，记录 Sentry 后**重新抛出**（让事务回滚）
- audit_log 记录失败事件 `promotion.review.event_dispatch_failed`

### 6.3 升级路径（V1+）
- **触发条件**：单 promotion 触发 ≥ 5 个监听器；或监听器涉及外部 API（如 U07 企微）
- **升级方案**：Outbox 模式（同事务写 outbox 表，异步 worker 投递到 Celery / Redis Streams）
- MVP 阶段（U04+U05）只有 1 个监听器，同事务足够

### 6.4 监控
- `settlement_requested_events_total{result="dispatched"}` ≈ `settlement_requested_events_total{result="no_handler"}` + handler_failed = 1（每次 review approve 必有一次分发）
- handler_failed > 0 即时告警

---

## 7. 演化兼容性

### 7.1 字段权限演化（U09）
所有 `# TODO U09` 标记位置 → grep `legacy_field_permissions` 替换。

### 7.2 状态机扩展
- U10b 假号判定可能新增 `is_suspected_fake_promotion` 字段（独立标记）
- 不影响现有 3 状态机

### 7.3 衍生字段优化
- 单租户突破 10 万行时，评估将 5 个衍生字段（urge_status / dual_platform / effective_like_count / is_hit / cpl）落地为 stored generated columns + 触发器维护
- 仅在性能不达 SLA 时考虑（避免提前优化）

### 7.4 跨平台扩展（V1+）
- platform 字段已预留 4 个枚举（小红书 / 抖音 / 快手 / B站）
- V1+ 视实际需要扩展具体业务字段

---

## 8. 数据迁移

### 8.1 5494 行历史数据
- **不在 U04 阶段实施**
- MVP 启用后由 U06d（推广导入适配器）通过 Excel 模板批量上传
- 调用 `PromotionService.create_promotion()` 公共 API（不需 upsert）
- 历史数据策略：
  - 沿用历史 internal_code（导入时跳过序列号生成）
  - 或重新生成 internal_code（按 cooperation_date 重新累加）
  - 由租户配置选择

### 8.2 Alembic Migration 执行
- 通过 `migrate.yml` 专用 job（与 U01-U03 一致）
- 先 staging 后 production

U04 单次 migration 内容：
- 创建 2 张表（promotion / promotion_sequence）
- 12+ 个索引（B-tree + GIN trgm）
- 启用 RLS 策略

---

## 9. 可恢复性

继承 U01 备份框架：
- daily/monthly tar.gz to R2
- promotion 表自动纳入
- 恢复演练通过 `restore_backup.py`

---

## 10. 测试覆盖需求

### 10.1 覆盖率门槛

| 文件 | 最低覆盖率 |
|---|---|
| `service.py` | ≥ 80% |
| `repository.py` | ≥ 70% |
| `domain.py` | ≥ 90% |
| `state_machines.py` | 100%（关键路径） |
| `urge_calculator.py` | 100%（双实现一致性测试） |
| `metrics_calculator.py` | ≥ 95% |
| `events.py` | ≥ 90% |
| `api.py` | ≥ 60% |

### 10.2 必须覆盖的集成测试场景（25 项）

| # | 场景 | 验收映射 |
|---|---|---|
| 1 | 创建 + internal_code 自动生成 | EP05-S02 |
| 2 | **序列号防 race**（并发 100 次创建，无重复 internal_code） | BR-U04-02 |
| 3 | 重复检测 warning 不阻塞 | EP05-S04 |
| 4 | urge_status 7 个 GWT 场景 | EP05-S06 |
| 5 | **urge_status Python vs SQL 一致性**（100 mock 数据） | BR-U04-30 |
| 6 | publish 同事务推进 settlement_status="待核查" | BR-U04-22 |
| 7 | 取消已发布 → 422 | EP05-S08 |
| 8 | 召回流程（start/success/failure + 跨状态机） | EP05-S09 |
| 9 | 平台折算系数 + 历史不重算 | EP05-S10 |
| 10 | 爆文阈值实时计算 | EP05-S11 |
| 11 | cpl + 零分母 null | EP05-S12 |
| 12 | 审核 approve → SettlementRequested 事件 mock 监听器 | EP05-S13 |
| 13 | **审核 approve 不创建 settlement**（mock SettlementService 断言未调） | INCEPTION 决策 |
| 14 | 审核 reject + reason → settlement_status="已驳回" | EP05-S13 |
| 15 | **自审禁止**（pr_id == reviewer.id → 422） | BR-U04-64 |
| 16 | **U05 监听器失败回滚**（mock 抛异常，断言 promotion 也回滚） | BR-U04-73 |
| 17 | 字段权限矩阵（4 角色 × quote_amount 读 + 写） | BR-U04-51/52 |
| 18 | update_like_count 区分 crawler/user audit | BR-U04-63 |
| 19 | 多租户隔离回归 | EP01-S07 |
| 20 | 事件总线 dispatch 多监听器 | BR-U04-70 |
| 21 | 跨状态机校验（recall 启动前 publish_status） | BR-U04-21/24 |
| 22 | internal_code 9999 溢出 → 409 | BR-U04-01 |
| 23 | 列表 CTE 计算字段（urge_status / dual_platform） | BR-U04-30/34 |
| 24 | 性能基准 `test_promotion_list_perf_with_10k_records` | NFR §3 |
| 25 | **端到端**：创建 → publish → 审核 → SettlementRequested 事件 → U05 创建 settlement（集成测试） | 端到端 |

### 10.3 性能基准
- `tests/performance/test_promotion_list_perf.py`：10K promotion + CTE，P95 ≤ 300ms
- `tests/performance/test_promotion_sequence_concurrent.py`：100 并发创建，无 race

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| 全部继承 U01-U03 NFR 基线 + 增量明确 | ✅ |
| 状态机性能用乐观并发（无悲观锁） | ✅ |
| 序列号 1 QPS 下行级锁完全可接受 | ✅ |
| CTE 性能依赖复合索引 + nightly 性能测试 | ✅ |
| 事件总线同事务一致性 + 失败回滚 | ✅ |
| 字段权限模式与 U02/U03 一致 | ✅ |
| 审计敏感字段脱敏（quote_amount/cost_snapshot） | ✅ |
| 监控双源（Prometheus 主 + Sentry 抽样） | ✅ |
| 自审禁止 + U05 失败回滚关键测试覆盖 | ✅ |
| migration 通过专用 job | ✅ |
