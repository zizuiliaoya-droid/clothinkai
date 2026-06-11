# U08 基础设施设计（Infrastructure Design）

> 单元：U08 — 发文进度看板
> 结论：**零基础设施增量**（纯读聚合层），复用 U01 既有 Zeabur 拓扑

---

## 1. 零增量声明

| 维度 | U08 是否新增 |
|---|---|
| Zeabur 服务 | ❌ 无（挂现有 backend 服务） |
| 数据库表 / migration | ❌ 无（聚合 promotion + style 现有表） |
| 数据库索引 | ❌ 无（复用 U04 索引；可选优化见 §4） |
| 环境变量 / Secret | ❌ 无 |
| Celery 任务 / Beat | ❌ 无（纯同步只读） |
| R2 桶 / 外部调用 | ❌ 无 |
| Redis 用量 | ❌ 无（MVP 不缓存聚合结果） |

U08 = 在现有 backend 服务上新增 4 个只读 GET 端点（`/api/reports/publish-progress/*`）。

---

## 2. 服务拓扑（复用）

| 服务 | U08 涉及 | 说明 |
|---|---|---|
| backend | ✅ | 4 GET 聚合端点（api 子域） |
| postgres | ✅（只读） | 聚合 promotion + style + user |
| frontend | （间接） | 三层看板 UI（Code Generation 视情补最小页面） |
| celery-worker / beat / redis | ❌ | 不涉及 |

---

## 3. 聚合查询负载特征

- summary：单条无 GROUP BY 聚合（COUNT/SUM + FILTER + URGE_EXPR），万级 promotion P95 ≤ 500ms。
- cards：GROUP BY style_id + JOIN style，分页 LIMIT/OFFSET。
- detail：限定 style_id，数据量小。
- 只读走 app 引擎 + RLS（依赖 Session 注入 tenant_id）；不用 bypass。

---

## 4. 索引复用 + 可选优化

- 复用 U04 promotion 索引：`(tenant_id, cooperation_date)` 类 + `style_id` + `pr_id`。
- **可选优化（不强制，非 U08 范围）**：若生产监控显示 cards GROUP BY style_id 慢，可补
  `idx_promotion_tenant_coop_style (tenant_id, cooperation_date, style_id)`（单独 migration，V1 评估）。
- MVP 不预聚合 / 不物化视图；V1 高频访问再评估。

---

## 5. CI/CD 影响

- U08 单元/集成/API 测试纳入既有 pytest job（聚合正确性 + TimeRange + null + 多租户，全部用现有测试 DB，无外部依赖）。
- 无新 CI job / 无新 secret / 无 migration job 改动。

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新增 Zeabur 服务 | ✅ §1 |
| 无 migration / 表 / 索引（复用） | ✅ §1 / §4 |
| 无环境变量 / Secret / Celery | ✅ §1 |
| 只读 app 引擎 + RLS | ✅ §3 |
| 聚合 SLA 负载特征 | ✅ §3 |

> 注：本文件经 Kiro spec-format 检测可能报「Missing ## Overview/## Architecture/...」= 已知假阳性（AI-DLC 格式 ≠ Kiro 模板），IGNORE。
