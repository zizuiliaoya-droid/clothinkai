# U10a 技术栈决策（Tech Stack Decisions）

> 单元：U10a — 设计制版全流程
> 原则：复用 U01-U09 技术栈，**零新增运行时依赖**；migration 013 建 4 表 + scope seed

---

## 1. 依赖确认（无新增）

| 用途 | 库 | 状态 |
|---|---|---|
| 状态机 | core/state_machine（StateMachine + TransitionRule） | ✅ 复用 |
| 文件存储 | core/attachment（R2 public/private） | ✅ 复用 |
| 通知 | U07 NotificationService | ✅ 复用 |
| JSONB 子表 | SQLAlchemy postgresql.JSONB | ✅ 既有 |
| Decimal 核价 | stdlib decimal | ✅ |

> requirements.txt 不改动。

---

## 2. DesignStateMachine（modules/design/state_machines.py）

- 复用 core/state_machine 基类；转移表声明 from→action→to + actor_roles + required_fields 守卫。
- 状态推进经 repository `update_design_status(style_id, from_status, to_status)` = `UPDATE style SET design_status=:to WHERE id=:id AND design_status=:from RETURNING`（乐观并发；None → StateTransitionConflict 409）。
- 驳回回退映射常量 `REJECT_PREVIOUS = {制版中:设计中, 工艺录入:制版中, 待补全:工艺录入, 待核价:待补全}`。

---

## 3. 自动核价（DesignService.submit_costing）

```python
total = cost_breakdown.fabric_cost + accessory_cost + craft_cost  # Decimal
# 系统口径写所有 active SKU（绕过 U09 字段写校验）
await sku_repo.bulk_update_cost_price(style_id, total)  # UPDATE WHERE style_id+is_active
await audit.log("design.auto_costing", resource="style", after={"cost_price_changed": True})
```

---

## 4. 文件存储落点

| 文件 | 桶 | 复用 |
|---|---|---|
| 设计稿主图 | R2 public | U02 main_image_key 规约 |
| 版型文件 pattern_file | R2 private | U05 签名 URL 模式（attachment + 签名 900s） |

---

## 5. 通知（复用 U07 + 角色解析）

- 新增 `RoleRepository.list_user_ids_by_role_code(role_code) -> list[UUID]`（join role+user_role，租户内 active）。
- DesignService 推进后：`user_ids = list_user_ids_by_role_code(next_role)` → `NotificationService.notify(user_ids, content, link, type=DESIGN_*)`（同事务，不自 commit）。
- NotificationType 追加 DESIGN_ADVANCE / DESIGN_REJECT / DESIGN_DONE（modules/wecom/enums.py）。

---

## 6. migration 013（4 表 + scope seed）

```python
# 013_u10a_create_design_tables.py（接 012）
# 4 表：style_fabric / style_pattern / style_craft / design_workflow_log
#   均 tenant_id + RLS + UNIQUE(style_id)（前 3 表 1:1）+ FK(style) ondelete CASCADE
#   design_workflow_log: idx(tenant_id, style_id, created_at)
# scope seed（绑角色，幂等 ON CONFLICT DO NOTHING）：
#   design.design:read|write → designer/admin*; design.pattern:read|write → pattern_maker
#   design.craft:write / design.tag_price:write / design.confirm_price:approve → merchandiser
#   design.costing:write → design_assistant
#   + design.*:read 给全设计角色 + operations(只读)
```

---

## 7. 组件落点

| 组件 | 路径 |
|---|---|
| 模块基础 | modules/design/{__init__,enums,permissions,exceptions}.py |
| 模型 | modules/design/models.py（StyleFabric/StylePattern/StyleCraft/DesignWorkflowLog） |
| Schema | modules/design/schemas.py |
| 状态机 | modules/design/state_machines.py |
| domain | modules/design/domain.py（核价求和 + 回退映射 + available_actions） |
| repository | modules/design/repository.py（子表 upsert + update_design_status + workflow_log + bulk SKU update） |
| service | modules/design/service.py（DesignService 13 方法） |
| deps/api | modules/design/{deps,api}.py（~13 端点） |
| 横切改动 | wecom/enums.py(+DESIGN_*) / auth/repository.py(+list_user_ids_by_role_code) / main.py(注册 design_router) |
| migration | alembic/versions/013_u10a_create_design_tables.py |

---

## 8. 测试落点

| 文件 | 类型 |
|---|---|
| tests/unit/test_design_state_machine.py | 转移表 + 非法 + 回退映射 + available_actions |
| tests/unit/test_design_costing.py | 核价求和 |
| tests/integration/test_design_workflow.py | J1 端到端 + 角色权限 + reject + cancel |
| tests/integration/test_design_notification.py | 通知写入 + 角色解析 |
| tests/api/test_design_api.py | 鉴权 + OpenAPI |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ §1 |
| 状态机复用 core + 乐观并发 | ✅ §2 |
| 自动核价系统口径 + audit | ✅ §3 |
| 文件 R2 public/private 复用 | ✅ §4 |
| 通知复用 U07 + 角色解析新增方法 | ✅ §5 |
| migration 013 4 表 + scope seed 幂等 | ✅ §6 |
