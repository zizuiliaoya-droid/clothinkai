# U10a 部署架构（Deployment Architecture）

> 单元：U10a — 设计制版全流程
> 结论：无服务/拓扑变更；部署 = 代码 + migration 013（4 表 + scope seed）

---

## 1. 部署拓扑（无变更）

```
[frontend] [backend] [celery-worker] [celery-beat] [postgres] [redis]
                ▲ U10a 仅改动 backend 代码 + migration 013（4 表 + design.* scope seed）
```
- U10a 不引入新服务、不改服务规格、不改网络/域名/TLS/Celery。

## 2. 部署 checklist

| # | 步骤 | 说明 |
|---|---|---|
| 1 | 合并 U10a 代码到 main | modules/design 全套 + wecom/enums + auth/repository + main 注册 |
| 2 | migrate.yml `alembic upgrade head` | 应用 migration 013（4 表 + RLS + design.* scope seed） |
| 3 | backend 自动部署（main → prod） | 复用 deploy-prod.yml |
| 4 | 冒烟验证（见 §3） | 状态机端到端 + 通知 + 自动核价 |

## 3. 部署后验证

| 验证 | 方法 | 期望 |
|---|---|---|
| 4 表已建 | `\dt style_fabric style_pattern style_craft design_workflow_log` | 4 表存在 + RLS 启用 |
| design.* scope seed | `SELECT count(*) FROM permission WHERE scope LIKE 'design.%'` | ≥ 7 |
| create_design | designer POST /api/designs/ | 201 + design_status="设计中" |
| 状态推进 | submit_fabric → grading → craft → costing → confirm_price | 逐级推进至"大货" |
| 自动核价 | submit_costing 后查 SKU | cost_price = 分项求和 |
| 通知 | 推进后查下一角色 unread-count | +1 |
| reject 回退 | 制版中 reject | 回到"设计中" + 通知设计师 |
| cancel | admin cancel | "已取消"，再推进 → 422 |
| 非法转移 | 设计中直接 confirm_price | 422 |

## 4. 回滚步骤

| 场景 | 操作 |
|---|---|
| 代码问题 | 回滚 backend（移除 design_router；style.design_status 既有数据不受影响） |
| migration 问题 | `alembic downgrade -1`（删 4 表 CASCADE + 删 design.* 细分 scope） |
| 缓存 | 无（design 不使用权限缓存外的额外缓存） |

## 5. 本地验证

```bash
# Docker PG16 + Redis7（U10a 用端口 5552/6407）
alembic upgrade head            # 应用 001→013
pytest tests/unit/test_design_state_machine.py tests/unit/test_design_costing.py \
       tests/integration/test_design_workflow.py tests/integration/test_design_notification.py \
       tests/api/test_design_api.py -p no:postgresql -m "not rls and not performance"
```

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新服务/拓扑变更 | ✅ §1 |
| 部署 = 代码 + migration 013 | ✅ §2 |
| 验证覆盖状态机端到端 + 通知 + 核价 | ✅ §3 |
| 回滚无数据迁移风险 | ✅ §4 |
| 本地 Docker 端口 5552/6407 | ✅ §5 |
