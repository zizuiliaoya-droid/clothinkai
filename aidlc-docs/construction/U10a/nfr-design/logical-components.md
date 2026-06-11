# U10a 逻辑组件（Logical Components）

> 单元：U10a — 设计制版全流程
> 新建 modules/design 全套 + migration 013（4 表 + scope seed）+ 3 处横切改动

---

## 1. 新建组件（modules/design/）

| 文件 | 职责 |
|---|---|
| `__init__.py` | 模块标识 |
| `enums.py` | DesignStatus（7 态） |
| `permissions.py` | design.* scope 常量 |
| `exceptions.py` | DesignStatusError 等（复用 core IllegalStateTransitionError/ValidationError） |
| `models.py` | StyleFabric / StylePattern / StyleCraft / DesignWorkflowLog（均 TenantScopedModel） |
| `schemas.py` | FabricSubmit / PatternSubmit / GradingSubmit / CraftSubmit / CostingSubmit / RejectRequest / CancelRequest / DesignDetailResponse / DesignListResponse |
| `state_machines.py` | DESIGN_TRANSITIONS + DesignStateMachine + REJECT_PREVIOUS + DRIVEN_BY + NOTIFY_ROLE |
| `domain.py` | 核价求和 + compute_available_actions + 回退/通知映射 |
| `repository.py` | upsert_fabric/pattern/craft + update_design_status（乐观并发 RETURNING）+ add_workflow_log + bulk_update_sku_cost_price + bulk_update_sku_tag_price + list_grouped + get_detail |
| `service.py` | DesignService（13 方法：create/submit_fabric/pattern/grading/craft/complete_fabric/costing/tag_price/confirm_price/reject/cancel/list/detail） |
| `deps.py` | 鉴权 + 权限依赖 |
| `api.py` | ~13 端点（POST /api/designs + PUT 子动作 + GET list/detail） |

## 2. 修改组件

| 组件 | 改动 |
|---|---|
| `modules/wecom/enums.py` | NotificationType +DESIGN_ADVANCE/DESIGN_REJECT/DESIGN_DONE |
| `modules/auth/repository.py` | RoleRepository +list_user_ids_by_role_code（join role+user_role，租户内 active） |
| `app/main.py` | 注册 design_router（/api 前缀） |
| `alembic/versions/013_u10a_create_design_tables.py` | 4 表 + RLS + scope seed（新建） |

## 3. 复用组件

| 复用 | 来源 | 用途 |
|---|---|---|
| StyleService / StyleRepository / SkuRepository | U02 | create_style + SKU cost/tag 更新 |
| NotificationService | U07 | 推进/驳回通知 |
| core/state_machine | U04/U05 | DesignStateMachine 基类 |
| core/attachment | U02/U05 | 设计稿 public / 版型 private + 签名 URL |
| AuditService | U01 | reject/cancel/核价审计 |
| RoleRepository.list_codes_for_user | U01 | actor 角色校验 |

## 4. 依赖图

```
modules/design/api
  → DesignService
      → DesignStateMachine (core/state_machine)
      → repository (4 子表 + 乐观并发 update_design_status + bulk SKU)
      → StyleService/StyleRepository/SkuRepository (U02)
      → NotificationService (U07) + RoleRepository.list_user_ids_by_role_code (U01)
      → core/attachment (R2 public/private)
      → AuditService (U01)
```
- 无循环依赖：design 依赖 product/auth/wecom/core，被依赖方不反向依赖 design。

## 5. migration 013

| 表 | 关键约束 |
|---|---|
| style_fabric | tenant_id + RLS + UNIQUE(style_id) + FK(style) CASCADE |
| style_pattern | 同上 + pattern_no |
| style_craft | 同上 |
| design_workflow_log | tenant_id + RLS + FK(style) + idx(tenant_id, style_id, created_at) |

scope seed（绑角色，幂等 ON CONFLICT DO NOTHING）：
- design.design:read|write → designer；design.pattern:read|write → pattern_maker
- design.craft:write / design.tag_price:write / design.confirm_price:approve → merchandiser
- design.costing:write → design_assistant
- design.*:read 已通过 designer DESIGN_ALL / operations 既有覆盖（按需补 read scope）

## 6. 测试文件

| 文件 | 类型 | 覆盖 |
|---|---|---|
| tests/unit/test_design_state_machine.py | 单元 | 全合法转移 + 非法 422 + REJECT_PREVIOUS + available_actions |
| tests/unit/test_design_costing.py | 单元 | 核价求和 + 分项非法 |
| tests/integration/test_design_workflow.py | 集成 | J1 端到端 + 角色权限 403 + reject 回退 + cancel 不可逆 + 乐观并发 |
| tests/integration/test_design_notification.py | 集成 | 通知写入 + 角色解析 + 无人跳过 + 自动核价写 SKU |
| tests/api/test_design_api.py | API | 鉴权 401/403 + OpenAPI |

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| modules/design 全文件 + migration 013 | ✅ §1/§5 |
| 横切 3 改动（wecom enums / auth repo / main） | ✅ §2 |
| 复用 U02/U07/core 无新依赖 | ✅ §3 |
| 无循环依赖 | ✅ §4 |
| 4 表 RLS + scope seed 幂等 | ✅ §5 |
| 5 测试文件覆盖单元/集成/API | ✅ §6 |
| 与 P-U10a-01/02/03 一致 | ✅ |
