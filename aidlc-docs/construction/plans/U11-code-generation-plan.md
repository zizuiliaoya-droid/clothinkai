# U11 代码生成计划（Code Generation Plan）

> 单元：U11 — 博主智能标签 + 灰豚展示（EP04-S04~S08）
> 分批：**单批** + Build & Test（小单元，新建 4 + 修改 5 + migration 015）
> Build & Test：Docker PG16:5554 + Redis7:6409 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 阈值常量集中在 `modules/blogger/tag_config.py`（5 个：FOLLOWER_KOC_MIN/KOL_MIN/FAKE_RATIO_THRESHOLD/HIGH_CPL_THRESHOLD/HIT_RATE_THRESHOLD）。
- [Answer] `compute_blogger_type` 实时 O(1)，在 BloggerService.create/update 设置 follower_count 后调用，写 blogger_type 字段。
- [Answer] `compute_read_like_ratio` 读时衍生（不存 DB），分母 0/None→None；BloggerResponse 新增 `read_like_ratio` 字段，`_to_response` 实时计算。
- [Answer] `is_fake_account`：ratio≤FAKE_RATIO_THRESHOLD→True；None（无数据）→False（保守不标记）。
- [Answer] `compute_quality_tags`：聚合 promotion 历史，avg CPL≤HIGH_CPL_THRESHOLD→"高性价比"；hit_rate≥HIT_RATE_THRESHOLD→"带货型"；多标签可叠加。
- [Answer] 聚合 `services/metric/blogger_quality.py`：avg_cpl_for_blogger / hit_rate_for_blogger 显式 WHERE tenant_id + LIMIT 1000 截断 + safe_div（复用 U08）；effective_like_count 复用 metrics_calculator 公式（Python 端折算）。
- [Answer] `recompute_all_blogger_tags` Celery 任务逐 tenant（system_context）+ 单 blogger try/except 失败不中止 + autoretry_for=(OperationalError,) max_retries=2。
- [Answer] recompute 端点 `POST /api/bloggers/recompute-tags` 管理员鉴权（新 scope `blogger.tag:recompute` seed 给 admin）。
- [Answer] migration 015：`ALTER TABLE blogger ADD COLUMN audience_profile JSONB NULL`（不锁表无回填）+ seed `blogger.tag:recompute` scope；downgrade DROP COLUMN + DELETE scope。
- [Answer] audience_profile 由 U13 采集 Worker 写入，U11 仅读展示。

---

## 1. 步骤

- [x] 1.1 modules/blogger/tag_config.py（5 阈值常量）
- [x] 1.2 services/metric/blogger_quality.py（avg_cpl_for_blogger / hit_rate_for_blogger / compute_quality_tags）
- [x] 1.3 modules/blogger/tag_service.py（BloggerTagService：compute_blogger_type/compute_read_like_ratio/is_fake_account/recompute_for_tenant）
- [x] 1.4 modules/blogger/models.py（Blogger +audience_profile JSONB nullable）
- [x] 1.5 modules/blogger/schemas.py（BloggerResponse +audience_profile +read_like_ratio）
- [x] 1.6 modules/blogger/service.py（create/update 调用 compute_blogger_type；_to_response +2 字段；替换 4 NotImplementedError 钩子）
- [x] 1.7 modules/blogger/api.py（+POST /api/bloggers/recompute-tags admin）
- [x] 1.8 tasks/blogger_tasks.py（recompute_all_blogger_tags Celery 任务）
- [x] 1.9 app/core/celery_app.py（autodiscover +tasks.blogger_tasks；Beat 选装注释）
- [x] 1.10 alembic/versions/015_u11_add_audience_profile.py（ALTER + scope seed）
- [x] 1.11 tests/unit/test_blogger_tag_service.py
- [x] 1.12 tests/integration/test_blogger_recompute.py
- [x] 1.13 tests/api/test_blogger_tag_api.py

### Build & Test
- [x] B.1 Docker PG16:5554 + Redis7:6409；alembic upgrade head（含 015）；U11 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部步骤 + Build & Test。**
