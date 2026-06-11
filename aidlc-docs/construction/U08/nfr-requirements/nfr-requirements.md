# U08 非功能需求（NFR Requirements）

> 单元：U08 — 发文进度看板
> 范围：U08 特异性 NFR 增量（聚合查询性能 / TimeRange / 只读 RLS / null 安全）；通用 NFR 继承 U01-U07

---

## 1. 与基线的关系

### 1.1 完全继承
- 认证 / 授权 / 多租户 RLS 双引擎 / audit（U01）
- 监控（Prometheus + Sentry + structlog）/ 健康检查 / pytest 框架（U01）
- 错误码体系 + 全局 error handler（U01）

### 1.2 U08 增量
- **聚合查询性能**：summary / cards / detail SLA
- **TimeRange 解析**：5 preset + custom 边界（≤366 天）
- **只读 RLS**：app 引擎 + tenant 注入，无写/事务
- **null 安全**：safe_div（分母 0 → null）

### 1.3 不涉及
- 无新表 / 无 migration / 无 Celery 任务 / 无外部调用 / 无写操作 / 无凭据 / 零新增依赖。

---

## 2. 性能 NFR

### 2.1 SLA

| 路径 | 指标 | 目标 | 备注 |
|---|---|---|---|
| GET summary | P95 | ≤ 500ms | 单条聚合（COUNT/SUM + FILTER），万级 promotion |
| GET cards（分页） | P95 | ≤ 500ms | GROUP BY style_id + JOIN style，分页 LIMIT |
| GET styles/{id}/by-pr | P95 | ≤ 200ms | 限定 style_id，数据量小 |
| GET styles/{id}/by-time | P95 | ≤ 200ms | 限定 style_id，半月 bucket |

### 2.2 聚合策略
- MVP **实时聚合**（不预聚合 / 不物化）；命中 promotion `(tenant_id, cooperation_date)` 相关索引。
- 不新增索引（复用 U04）；Build & Test 发现卡片 GROUP BY style_id 慢则评估 `idx(tenant_id, cooperation_date, style_id)`（不强制）。
- V1 评估：高频访问改物化视图 + 定时刷新。

### 2.3 容量

| 对象 | MVP 预估 |
|---|---|
| 单租户 promotion | 万级（U04） |
| TimeRange 跨度 | ≤ 366 天（custom 上限，防全表扫描） |
| cards 分页 | page_size ≤ 100 |

---

## 3. 可靠性 / 正确性 NFR

- **只读**：无写操作、无事务边界问题、不触发事件；失败不影响数据。
- **null 安全**：所有比率（发布率 / 超时率）+ CPL 经 `safe_div`，分母 0 / None → null（前端"—"）；计数 / 金额用 COALESCE 归零。
- **统一日期**：`get_today()`（Asia/Shanghai，FB8）；SQL 不用 CURRENT_DATE，超时量复用 `URGE_STATUS_SQL_EXPR` 的 `:today` 参数。
- **空数据集**：返回零值汇总（count=0 / amount=0 / rate=null），不报错。

---

## 4. 安全 NFR

### 4.1 多租户
- 只读走 app 引擎 + RLS（依赖 Session 注入 tenant_id）；**不用 bypass**；聚合 SQL 不跨租户。
- NFR 测试：租户 A 的看板不含租户 B 数据。

### 4.2 权限
- `report.publish_progress:read`（pr 直含；pr_manager / operations 通过 report.*:read 通配覆盖）。
- 无写 / 删除端点。

### 4.3 输入校验
- TimeRange custom 跨度 ≤ 366 天（防 DoS 全表扫描）；非法 preset / from>to → 422。
- 不暴露敏感字段（看板为聚合数值，无 PII / 凭据）。

---

## 5. 可观测性 NFR

- **不新增自定义 Prometheus 指标**：HTTP 时延由 `prometheus-fastapi-instrumentator` 自动按 handler 暴露。
- structlog 记 report 查询：tenant_id / preset / date_from / date_to / 耗时（不记聚合明细）。
- Sentry：聚合 SQL 异常 capture。

---

## 6. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | safe_div（正常 / 分母 0 / None）/ TimeRange resolve（5 preset + custom 边界 + 跨度超限 422）/ 半月 bucket 计算 |
| 集成 | 构造已知 promotion 数据集 → summary 9 指标数值断言 / cards GROUP BY style 数值 / detail_by_pr / detail_by_time / 空数据集 rate=null / **多租户隔离** |
| API | 鉴权（report.publish_progress:read）/ OpenAPI / 非法 preset 422 |
| 覆盖率 | service ≥ 80% / metric ≥ 90% / api ≥ 60%（继承基线） |

---

## 7. 故事 NFR 映射

| 故事 | NFR 验收 |
|---|---|
| EP09-S01 三层看板 | summary/cards P95 ≤ 500ms + 分母 0→null + 多租户隔离 |
| EP09-S07 时间筛选 | 5 preset 解析正确 + custom 边界 422 + 按 cooperation_date 聚合 |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 / 表 / migration | ✅ §1.3 |
| 聚合 SLA 量化 | ✅ §2.1 |
| 实时聚合 + 复用索引 | ✅ §2.2 |
| null 安全（safe_div） | ✅ §3 |
| 只读 RLS + 权限 | ✅ §4 |
| TimeRange 边界（≤366 天） | ✅ §4.3 |
| 无新增自定义指标 | ✅ §5 |
| 多租户隔离测试 | ✅ §6 |
