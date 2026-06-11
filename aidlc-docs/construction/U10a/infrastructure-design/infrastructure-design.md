# U10a 基础设施设计（Infrastructure Design）

> 单元：U10a — 设计制版全流程
> 结论：无新服务 / 无新依赖 / 无新桶 / 无新环境变量；唯一增量 = migration 013（4 表 + design.* scope seed）

---

## 1. 基础设施增量总览

| 维度 | 增量 |
|---|---|
| Zeabur 服务 | 无（复用 backend） |
| 数据库表 | +4（style_fabric / style_pattern / style_craft / design_workflow_log） |
| 数据库扩展/索引 | design_workflow_log idx(tenant_id, style_id, created_at) |
| 依赖（requirements） | 无 |
| 环境变量 / Secrets | 无 |
| Celery 队列/任务 | 无（通知同步同事务） |
| R2 桶 | 无新增（设计稿 public / 版型 private 复用） |
| Prometheus 指标 | 无新增 |

---

## 2. migration 013（4 表 + scope seed）

```text
# alembic/versions/013_u10a_create_design_tables.py（接 012）
表 1 style_fabric
  id PK / tenant_id NOT NULL / style_id UUID NOT NULL UNIQUE FK(style) CASCADE
  fabrics JSONB / accessories JSONB / is_completed bool default false / remark text
  created_at / updated_at；RLS ENABLE + policy(tenant_id = current_setting('app.tenant_id'))
表 2 style_pattern
  ... style_id UNIQUE FK CASCADE / pattern_no varchar(64) / pattern_file_key varchar(256) / grading_data JSONB
表 3 style_craft
  ... style_id UNIQUE FK CASCADE / craft_info JSONB
表 4 design_workflow_log
  id PK / tenant_id / style_id FK CASCADE / from_status varchar(16) / to_status varchar(16)
  action varchar(32) / driven_by varchar(32) / actor_id UUID / reason text / created_at
  idx(tenant_id, style_id, created_at)；RLS ENABLE + policy
```

- 4 表均 TenantScopedModel 风格（tenant_id + RLS）；前 3 表 1:1（UNIQUE(style_id)）。
- 全为新建空表，无回填；downgrade 删 4 表（CASCADE）。

---

## 3. design.* scope seed（绑角色，幂等）

| scope | 角色 |
|---|---|
| design.design:read / design.design:write | designer（+ admin 通配） |
| design.pattern:read / design.pattern:write | pattern_maker |
| design.craft:write / design.tag_price:write / design.confirm_price:approve | merchandiser |
| design.costing:write | design_assistant |
| design.design:read（只读看板） | operations（按需） |

- INSERT permission + role_permission，均 `ON CONFLICT DO NOTHING`（幂等）。
- default_roles 既有部分 design.* 通配（DESIGN_ALL=design.*:* 给 designer/design_assistant；design.pattern:* 给 pattern_maker；design.craft:write/design.tag_price:write/design.confirm_price:approve 给 merchandiser）→ 013 补齐 permission 表行 + 细分绑定，确保 require_permission 命中。

---

## 4. R2 桶复用

| 文件 | 桶 | 规约 |
|---|---|---|
| 设计稿主图 | public | U02 main_image_key（style.main_image_key 既有字段） |
| 版型文件 pattern_file_key | private | U05 签名 URL（attachment + get_signed_url 900s） |

---

## 5. 复用清单

| 复用项 | 来源 |
|---|---|
| backend FastAPI / migrate.yml / ci.yml | U01 |
| style / sku 表 + StyleService/SkuRepository | U02 |
| notification 表 + NotificationService | U07 |
| core/state_machine / core/attachment / audit / RLS helper | U01/U02/U04/U05 |
| R2 public/private 桶 | U01/U05 |

---

## 6. 部署 / 回滚

- **部署**：代码 + migration 013 同批；migrate.yml `alembic upgrade head` 执行 013；4 表空表无回填，scope seed 幂等。
- **回滚**：代码回滚移除 design_router；migration 013 downgrade 删 4 表 + 删 design.* 细分 scope（role_permission 按 FK 连带）；style.design_status 既有数据不受影响。
- **零停机**：建表 + seed 不锁既有表。

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新服务/依赖/桶/环境变量/Celery | ✅ §1 |
| migration 013 = 4 表 + RLS + scope seed | ✅ §2/§3 |
| R2 public/private 复用 | ✅ §4 |
| 复用 U02/U07/core | ✅ §5 |
| 部署/回滚无回填风险 | ✅ §6 |

> 注：infrastructure-design.md 触发 spec-format 假阳性（Missing ## Overview/## Architecture 等）= 已知，IGNORE。
