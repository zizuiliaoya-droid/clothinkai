# U11 部署架构（Deployment Architecture）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 无服务/拓扑变更；部署 = 代码 + migration 015 + Celery 重启

---

## 1. 部署拓扑（无变更）

```
[frontend] [backend] [celery-worker] [celery-beat] [postgres] [redis]
                ▲ U11 改动 backend + celery-worker（新任务 autodiscover）+ migration 015
```

## 2. 部署 checklist

| # | 步骤 |
|---|---|
| 1 | 合并代码（tag_config/tag_service/blogger_quality/blogger_tasks + 修改 5 文件） |
| 2 | migrate.yml `alembic upgrade head`（015 ALTER） |
| 3 | backend + celery-worker 自动部署 |
| 4 | 冒烟验证 |

## 3. 部署后验证

| 验证 | 期望 |
|---|---|
| `SELECT column_name FROM information_schema.columns WHERE table_name='blogger' AND column_name='audience_profile'` | 存在 |
| 创建 follower_count=50000 博主 | blogger_type = "KOC" |
| GET /api/bloggers/{id} | 含 audience_profile=null + read_like_ratio=null |
| admin POST /api/bloggers/recompute-tags | 202 / Celery 任务入队 |
| 博主有 audience_profile 数据 | read_like_ratio 计算正确 |

## 4. 回滚

| 场景 | 操作 |
|---|---|
| 代码 | 回滚 backend + celery-worker |
| migration | `alembic downgrade -1`（DROP COLUMN audience_profile） |

## 5. 本地验证

```bash
# Docker PG16 + Redis7（U11 用端口 5554/6409）
alembic upgrade head  # 001→015
pytest tests/unit/test_blogger_tag_service.py \
       tests/integration/test_blogger_recompute.py \
       tests/api/test_blogger_tag_api.py \
       -p no:postgresql -m "not rls and not performance"
```

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新服务/拓扑 | ✅ |
| migration 015 + Celery autodiscover | ✅ |
| 验证覆盖 type/ratio/recompute | ✅ |
| 回滚安全 | ✅ |
