# U11 非功能需求（NFR Requirements）

> 单元：U11 — 博主智能标签 + 灰豚展示；通用 NFR 继承 U01-U10b

---

## 1. 与基线的关系

### 1.1 完全继承
- 四层架构 / RLS / audit / error handler / structlog / pytest / Celery 框架（U01/U06a）

### 1.2 U11 增量
- BloggerTagService（tag_service.py）+ blogger_quality.py（services/metric）
- Celery 任务 `recompute_all_blogger_tags`
- ALTER blogger ADD audience_profile（migration 015）
- BloggerResponse 追加 2 字段

### 1.3 不涉及
- 无新依赖 / 无新表（仅 ALTER 1 列）/ 无外部调用 / 无新 Prometheus 指标。

---

## 2. 性能 NFR

| 路径 | 指标 | 目标 |
|---|---|---|
| compute_blogger_type（同步） | — | O(1) 内存比较，无 DB |
| read_like_ratio（读时） | — | O(1) 内存（JSONB 已在行内） |
| quality_tags 聚合（promotion） | P95 | ≤ 200ms（idx blogger_id + LIMIT 1000） |
| recompute 任务（后台） | 全量 1 万 blogger | ≤ 10min（凌晨，不影响在线） |
| GET detail 含新字段 | P95 | ≤ 200ms（无额外 JOIN，同行 JSONB） |

---

## 3. 可靠性 NFR

- Celery 任务 autoretry_for=(OperationalError,)，max_retries=2，延迟 10s（网络/DB 瞬断）。
- recompute 单 blogger 失败不中止整个任务（catch + log + 继续）。

---

## 4. 安全 NFR

| 威胁 | 防护 |
|---|---|
| 客户端伪造 ratio / tags | BloggerUpdate schema 不含 audience_profile / read_like_ratio（服务端计算） |
| 越权触发 recompute | POST /api/bloggers/recompute-tags 仅 admin（* 通配） |
| 跨租户计算污染 | recompute 逐 tenant system_context + promotion 聚合显式 WHERE tenant_id |

---

## 5. 多租户 NFR

- recompute 任务逐 tenant 执行（system_context）；promotion 聚合显式 tenant 过滤。
- audience_profile 写入由 U13 保证同租户（U11 仅读）。

---

## 6. migration NFR

- migration 015（接 014）：`ALTER TABLE blogger ADD COLUMN audience_profile JSONB NULL`；无新表，无回填。

---

## 7. Celery NFR

- 新增任务 `tasks/blogger_tasks.py::recompute_all_blogger_tags`。
- celery_app autodiscover 路径追加 `tasks.blogger_tasks`。
- Beat 每日 02:00 选装（默认注释/配置关闭）。

---

## 8. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | compute_blogger_type 阈值边界 / ratio 分母 0 / is_fake / quality_tags 多条件 / audience_profile null |
| 集成 | recompute 全流程（含 promotion 聚合）/ 实时 type 触发 / detail 含新字段 |
| API | recompute 端点 admin 403 / detail audience_profile 展示 |
| 覆盖率 | tag_service ≥ 90% / blogger_quality ≥ 90% |

---

## 9. 故事 NFR 映射

| 故事 | NFR 验收 |
|---|---|
| EP04-S04 | type 实时 O(1) + 阈值可调 recompute |
| EP04-S05 | ratio 分母 0→null + 无 audience→null |
| EP04-S06 | fake 判定 + recompute 全量 |
| EP04-S07 | quality CPL 聚合 ≤200ms + 多标签 |
| EP04-S08 | audience_profile null 安全展示 |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| recompute 后台不影响在线 | ✅ §2 |
| 客户端不可伪造 | ✅ §4 |
| migration 015 仅 ALTER 1 列 | ✅ §6 |
| Celery autodiscover + 选装 Beat | ✅ §7 |

> nfr-requirements.md spec-format 假阳性 IGNORE。
