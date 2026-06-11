# U11 逻辑组件（Logical Components）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 新建 3 文件 + 修改 4 + migration 015

---

## 1. 新建组件

| 文件 | 职责 |
|---|---|
| `modules/blogger/tag_config.py` | 5 个阈值常量 |
| `modules/blogger/tag_service.py` | BloggerTagService（compute_type/ratio/fake/quality/recompute） |
| `services/metric/blogger_quality.py` | avg_cpl_for_blogger / hit_rate_for_blogger 聚合 |
| `tasks/blogger_tasks.py` | recompute_all_blogger_tags Celery 任务 |

## 2. 修改组件

| 组件 | 改动 |
|---|---|
| `modules/blogger/service.py` | create/update 追加 compute_and_save_type 调用 |
| `modules/blogger/schemas.py` | BloggerResponse +audience_profile +read_like_ratio |
| `modules/blogger/api.py` | +POST /api/bloggers/recompute-tags（admin）|
| `app/core/celery_app.py` | autodiscover 追加 `tasks.blogger_tasks` |
| `alembic/versions/015_u11_add_audience_profile.py` | ALTER blogger ADD audience_profile JSONB NULL |

## 3. 复用组件

| 复用 | 来源 |
|---|---|
| Blogger model / BloggerRepository | U03 |
| PromotionRepository（聚合 CPL/hit） | U04 |
| services/metric/common.safe_div | U08 |
| celery_app + system_context | U01 |
| AuditService | U01 |

## 4. 依赖图

```
blogger/api (recompute endpoint)
  → BloggerTagService
      → tag_config (常量)
      → services/metric/blogger_quality (聚合)
          → PromotionRepository (U04)
      → BloggerRepository (U03)
  → BloggerService (实时触发 type)

tasks/blogger_tasks
  → BloggerTagService.recompute_for_tenant
  → system_context (U01)
```
- 无循环依赖。

## 5. migration 015

```text
ALTER TABLE blogger ADD COLUMN audience_profile JSONB NULL;
-- downgrade: ALTER TABLE blogger DROP COLUMN audience_profile;
```

## 6. 测试文件

| 文件 | 类型 |
|---|---|
| tests/unit/test_blogger_tag_service.py | 阈值边界 + ratio + fake + quality |
| tests/integration/test_blogger_recompute.py | Celery 全流程 + promotion 聚合 |
| tests/api/test_blogger_tag_api.py | recompute 鉴权 + detail 新字段 |

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 新建 4 + 修改 5 + migration 015 | ✅ |
| 复用 U03/U04/U08/U01 | ✅ |
| 无循环依赖 | ✅ |
| 与 P-U11-01/02 一致 | ✅ |
