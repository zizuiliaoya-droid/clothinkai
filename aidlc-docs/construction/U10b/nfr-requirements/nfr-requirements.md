# U10b 非功能需求（NFR Requirements）

> 单元：U10b — 平台商品映射；极小增量，通用 NFR 继承 U01-U10a

---

## 1. 与基线的关系

### 1.1 完全继承
- 四层架构 / RLS 多租户 / audit / 全局 error handler / structlog / pytest（U01）

### 1.2 U10b 增量
- platform_product 表（migration 014）+ PlatformProductService + 端点
- product.platform:read/write scope seed

### 1.3 不涉及
- 无新依赖 / 无 Celery / 无外部调用 / 无新自定义指标 / 无新桶。

---

## 2. 可靠性 / 并发 NFR

| 路径 | 要求 |
|---|---|
| create 唯一性 | DB UNIQUE(tenant, platform, platform_id) 兜底；并发重复 → IntegrityError 捕获转 409（不依赖先查后插 TOCTOU） |
| create_or_update | SELECT-then-insert/update（导入路径串行，冲突重试可接受）；幂等 |
| 引用校验 | style/sku get 经 RLS 返回 None → 422（防跨租户挂接） |

---

## 3. 性能 NFR

| 路径 | 指标 | 目标 |
|---|---|---|
| find_by_platform_id | P95 | ≤ 100ms（命中 UNIQUE 索引 tenant+platform+platform_id） |
| 按款式反查 | P95 | ≤ 100ms（idx tenant+style_id） |
| create / update / delete | P95 | ≤ 150ms |
| list | P95 | ≤ 200ms（分页默认 20） |

- 容量：单租户平台商品 ≤ 数万；无重计算 / 无聚合。

---

## 4. 安全 NFR

| 威胁 | 防护 |
|---|---|
| 越权写映射 | require_permission(product.platform:write) |
| 跨租户挂接 style/sku | 引用校验（get 经 RLS 本租户）→ 不存在 422；FK 同库约束 |
| 审计 | create/update/delete 写 audit_log（platform/platform_id/style_id） |

---

## 5. 多租户 NFR

- platform_product TenantScopedModel + RLS 启用。
- 列表/反查显式 `WHERE tenant_id`（防御 + 测试 bypass 确定性，同既有约定）。

---

## 6. migration NFR

- migration 014（接 013）：platform_product 表（1 表 + RLS + UNIQUE(tenant,platform,platform_id) + FK(style RESTRICT, sku SET NULL) + idx(tenant,style_id)）+ product.platform:read/write scope seed（绑 merchandiser/operations，幂等）。

---

## 7. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 集成 | create 成功 / 重复 409 / create_or_update 幂等（insert+update）/ find_by_platform_id 命中+未命中 None / style 引用 422 / sku 不属于 style 422 / 跨租户隔离 / 删除 |
| API | 鉴权 401/403 + OpenAPI |
| 覆盖率 | service ≥ 80%（继承基线） |

---

## 8. 故事 NFR 映射

| 故事 | NFR 验收 |
|---|---|
| EP02-S07 | UNIQUE 并发 409 + 反查 ≤100ms + 引用校验防跨租户 + 导入幂等关联 |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ §1.3 |
| 唯一约束 IntegrityError→409 | ✅ §2 |
| 反查命中 UNIQUE 索引 | ✅ §3 |
| 引用校验防跨租户 + audit | ✅ §4 |
| migration 014 1 表 + scope seed | ✅ §6 |

> 注：nfr-requirements.md 触发 spec-format 假阳性（Missing ## Introduction/## Requirements）= 已知，IGNORE。
