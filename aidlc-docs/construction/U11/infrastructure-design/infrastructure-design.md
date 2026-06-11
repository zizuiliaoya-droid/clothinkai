# U11 基础设施设计（Infrastructure Design）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 结论：零新服务/表/桶/依赖/环境变量；唯一增量 = migration 015（ALTER 1 列）+ Celery Beat 选装

---

## 1. 基础设施增量总览

| 维度 | 增量 |
|---|---|
| Zeabur 服务 | 无 |
| 数据库表 | 无新表；ALTER blogger ADD audience_profile JSONB NULL |
| 依赖 | 无 |
| 环境变量 | 无 |
| Celery | +1 任务（recompute_all_blogger_tags）；Beat 选装 02:00 |
| R2 桶 | 无 |
| Prometheus | 无新增 |

---

## 2. migration 015

```text
# alembic/versions/015_u11_add_audience_profile.py（接 014）
ALTER TABLE blogger ADD COLUMN audience_profile JSONB NULL;
-- downgrade: ALTER TABLE blogger DROP COLUMN audience_profile;
```
- 无回填；nullable 列追加不锁表。

---

## 3. Celery Beat 选装

```python
# celery_app.py beat_schedule（注释可启用）
# "recompute-blogger-tags": {
#     "task": "tasks.blogger_tasks.recompute_all_blogger_tags",
#     "schedule": crontab(hour=2, minute=0),
# },
```
- 默认关闭；admin 手动触发（POST /api/bloggers/recompute-tags）。

---

## 4. celery_app autodiscover

追加 `tasks.blogger_tasks` 到 autodiscover 路径列表。

---

## 5. 复用清单

| 复用项 | 来源 |
|---|---|
| backend + celery-worker + celery-beat | U01 |
| blogger 表 | U03 |
| promotion 表（聚合） | U04 |
| services/metric/common.safe_div | U08 |

---

## 6. 部署 / 回滚

- **部署**：代码 + migration 015 同批；ALTER 不锁表；Celery worker 重启后 autodiscover 新任务。
- **回滚**：migration 015 downgrade DROP COLUMN；代码回滚删 tag_service / blogger_tasks / recompute API。

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新服务/桶/依赖 | ✅ |
| migration 015 = ALTER 1 列 | ✅ |
| Celery Beat 选装 | ✅ |
| 部署/回滚无回填风险 | ✅ |

> infrastructure-design.md spec-format 假阳性 IGNORE。
