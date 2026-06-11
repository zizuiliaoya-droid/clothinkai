# U11 技术栈决策（Tech Stack Decisions）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 原则：复用 U01-U10b 技术栈，**零新增运行时依赖**；migration 015 = ALTER 1 列

---

## 1. 依赖确认（无新增）

| 用途 | 库 | 状态 |
|---|---|---|
| 标签计算 | stdlib Decimal / set | ✅ |
| 聚合 | SQLAlchemy func.avg / func.count | ✅ |
| Celery 任务 | celery（已有） | ✅ 复用 |
| JSONB 读写 | SQLAlchemy JSONB | ✅ |

---

## 2. 组件落点

| 组件 | 路径 |
|---|---|
| 阈值常量 | `modules/blogger/tag_config.py` |
| BloggerTagService | `modules/blogger/tag_service.py` |
| 聚合逻辑 | `services/metric/blogger_quality.py` |
| Celery 任务 | `tasks/blogger_tasks.py` |
| recompute API | `modules/blogger/api.py`（追加 1 端点） |
| BloggerResponse 扩展 | `modules/blogger/schemas.py`（+audience_profile + read_like_ratio） |
| migration | `alembic/versions/015_u11_add_audience_profile.py` |

---

## 3. BloggerTagService 关键方法

```python
class BloggerTagService:
    def compute_blogger_type(self, follower_count: int | None) -> str | None: ...
    def compute_read_like_ratio(self, audience_profile: dict | None) -> Decimal | None: ...
    def is_fake_account(self, ratio: Decimal | None) -> bool | None: ...
    async def compute_quality_tags(self, blogger_id, session) -> list[str]: ...
        # 聚合 promotion（avg CPL / hit_rate）
    async def recompute_for_tenant(self, tenant_id, session) -> int: ...
```

---

## 4. services/metric/blogger_quality.py

```python
async def avg_cpl_for_blogger(blogger_id, session, tenant_id) -> Decimal | None: ...
async def hit_rate_for_blogger(blogger_id, session, tenant_id) -> Decimal | None: ...
```
- 显式 WHERE tenant_id + blogger_id + promotion is_active。
- LIMIT 1000 截断（防超大历史）。

---

## 5. migration 015

```python
# 015_u11_add_audience_profile.py（接 014）
# ALTER TABLE blogger ADD COLUMN audience_profile JSONB NULL
# downgrade：DROP COLUMN audience_profile
```

---

## 6. Celery 任务注册

- `tasks/blogger_tasks.py`：`recompute_all_blogger_tags`（asyncio.run + system_context 逐 tenant + autoretry 2 次）。
- `celery_app.py` autodiscover 追加 `tasks.blogger_tasks`。
- Beat schedule（选装，默认注释）：`"recompute-blogger-tags": {"task": ..., "schedule": crontab(hour=2, minute=0)}`。

---

## 7. 测试落点

| 文件 | 类型 |
|---|---|
| tests/unit/test_blogger_tag_service.py | 阈值边界 + ratio + fake + quality |
| tests/integration/test_blogger_recompute.py | Celery 全流程 + promotion 聚合 |
| tests/api/test_blogger_tag_api.py | recompute 鉴权 + detail 新字段 |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| tag_service + blogger_quality + Celery 任务 | ✅ §2/§3/§4/§6 |
| migration 015 仅 ALTER 1 列 | ✅ §5 |
| 测试覆盖 | ✅ §7 |
