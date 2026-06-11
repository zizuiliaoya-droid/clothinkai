# U10b 部署架构（Deployment Architecture）

> 单元：U10b — 平台商品映射
> 无服务/拓扑变更；部署 = 代码 + migration 014

---

## 1. 部署拓扑（无变更）

```
[frontend] [backend] [celery-worker] [celery-beat] [postgres] [redis]
                ▲ U10b 仅改动 backend 代码 + migration 014（1 表 + scope seed）
```

## 2. 部署 checklist

| # | 步骤 |
|---|---|
| 1 | 合并 U10b 代码到 main（4 新文件 + permissions 追加 + main 注册） |
| 2 | migrate.yml `alembic upgrade head`（014） |
| 3 | backend 自动部署 |
| 4 | 冒烟验证 |

## 3. 部署后验证

| 验证 | 期望 |
|---|---|
| `\dt platform_product` | 表存在 + RLS 启用 |
| `SELECT count(*) FROM permission WHERE scope LIKE 'product.platform%'` | ≥ 2 |
| POST /api/platform-products （merchandiser） | 201 |
| 重复创建 | 409 |
| GET /api/platform-products/lookup?platform=qianniu&platform_id=123 | 返回映射或空 |
| style_id 不存在 | 422 |

## 4. 回滚

| 场景 | 操作 |
|---|---|
| 代码 | 回滚 backend（移除 platform_product router） |
| migration | `alembic downgrade -1`（删表 + 删 scope） |

## 5. 本地验证

```bash
# Docker PG16 + Redis7（U10b 与 U10a 同 Build & Test 端口 5553/6408）
alembic upgrade head  # 001→014
pytest tests/integration/test_platform_product.py tests/api/test_platform_product_api.py \
       -p no:postgresql -m "not rls and not performance"
```

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新服务 | ✅ |
| 部署 = 代码 + migration 014 | ✅ |
| 验证覆盖 CRUD + 409 + 反查 + 引用 422 | ✅ |
| 回滚无风险 | ✅ |
