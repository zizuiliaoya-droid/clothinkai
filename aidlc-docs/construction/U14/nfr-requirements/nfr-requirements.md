# U14 NFR 需求（NFR Requirements）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 增量式：复用 U01/U08 报表基线 + U13 采集数据

---

## 1. 依赖与复用

| 项 | 决策 |
|---|---|
| 新增第三方依赖 | **零**——聚合用 SQLAlchemy text() 原生 SQL；safe_div / resolve_time_range / like_sum_expr / URGE_STATUS_SQL_EXPR 全复用 |
| 新表 | 2 表（migration 018）：target_planning / store_daily |
| 新环境变量 | **无** |
| 新 Celery 队列/任务 | report 队列（shared-infrastructure 已预留）+ precompute_report_cache 占位（V1 不强制启用） |
| 新 R2 桶 | **无** |

---

## 2. 性能需求

| 报表 | SLA | 实现 |
|---|---|---|
| 工作进度（月度 GROUP BY pr） | P95 ≤ 500ms | promotion idx(cooperation_date) + FILTER 聚合 |
| 爆款约篇 | P95 ≤ 300ms | target_planning idx(month) + promotion 子查询聚合 |
| 店铺数据 | P95 ≤ 500ms | qianniu_daily idx(tenant,date) GROUP BY date + store_daily 左联 |
| 投产报表（4 表跨表 + 周环比 2 期） | P95 ≤ 800ms | 子查询预聚合 + idx 复用（最重） |

### 跨表聚合优化（投产报表）

| 措施 | 说明 |
|---|---|
| 子查询预聚合 | ad_daily / promotion 各自 GROUP BY style_id 后 JOIN style，避免笛卡尔积膨胀 |
| 索引复用 | qianniu_daily/ad_daily idx(tenant,date) + platform_product idx(tenant,style) + promotion idx(cooperation_date) |
| 周环比 | current + previous 两次独立聚合（等长上期），不在单 SQL 内做窗口 |
| 比率后处理 | safe_div 在 service 层（不在 SQL 内除），分母 0→null 语义统一 |

---

## 3. 容量需求

| 维度 | 假设 |
|---|---|
| 单租户款式 | ≤ 5 万 |
| qianniu_daily / ad_daily | 每日数百~数千行 × 365 天 |
| time_range 上限 | ≤ 366 天（resolve_time_range 限制） |
| 投产报表结果 | GROUP BY style ≤ 数千行（分页） |

---

## 4. 安全需求

| 项 | 措施 |
|---|---|
| 只读聚合 | 全部报表纯读；target_planning/store_daily 为配置写（非业务事件） |
| 多租户隔离 | RLS + 显式 WHERE tenant_id（bypass 测试防御层） |
| 读权限 | report.*:read 通配（operations/pr_manager 已有） |
| 写权限 | report.target:write（pr_manager 设目标）+ report.store_daily:write（operations 手动字段） |
| 字段级 | report 是聚合层，不暴露单条敏感字段；无额外字段屏蔽 |

---

## 5. 可靠性 / 数据语义

| 项 | 决策 |
|---|---|
| 除零 | 全部比率 safe_div（分母 0/None→null，前端 "—"） |
| 空值 | 金额空按 0 汇总；qianniu_daily 缺失 extra 字段 COALESCE 0/null |
| exclude_brushing | 参数存在 V1 默认 False 不影响结果（U16 启用） |
| 周环比 | previous 等长上期（date_from-span ~ date_from-1） |

---

## 6. 可观测性需求

| 指标 | 类型 | 标签 |
|---|---|---|
| `report_query_duration_seconds` | Histogram | report_type（work_progress/target/store_daily/production） |

---

## 7. 多租户隔离测试矩阵

| 场景 | 期望 |
|---|---|
| A 租户报表 | 不含 B 数据（RLS + 显式 tenant） |
| target_planning UNIQUE | 跨租户独立（A/B 同 pr/style/month 互不冲突） |
| store_daily UNIQUE | 跨租户独立（A/B 同 date 互不冲突） |
| 投产跨表聚合 | 不跨租户串数据 |

---

## 8. 数据迁移

| 项 | 决策 |
|---|---|
| migration 018 | target_planning + store_daily 2 表 + RLS + UNIQUE + idx + 6 scope seed |
| 回填 | 无（新表） |
| 回滚 | downgrade DROP 2 表 + DELETE scope |

---

## 9. 测试需求

| 类型 | 覆盖 | 关键场景 |
|---|---|---|
| 单元 | metric/domain ≥ 85% | 投产 5 公式 safe_div 边界（分母 0→null）+ 周环比上期计算 + 达标/缺口 |
| 集成 | service ≥ 80% | 工作进度月度聚合 + 爆款约篇 set/list + 店铺聚合+手动 upsert + 投产跨表 + RLS |
| API | ≥ 60% | 6 端点鉴权 + OpenAPI + 时间筛选 |
| 整体 | ≥ 70% | — |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| 4 报表 SLA + 跨表聚合优化 | ✅ §2 |
| 除零 safe_div + 周环比 | ✅ §5 |
| 1 指标 | ✅ §6 |
| 多租户隔离测试矩阵 | ✅ §7 |
| migration 018 + precompute 占位 | ✅ §8 |
| 与 functional-design EP09-S02~S05 一致 | ✅ |
