# U10a 功能设计计划（Functional Design Plan）

> 单元：U10a — 设计制版全流程（EP03-S02~S14）
> 依赖：U02（Style/Sku）、U07（NotificationService 复用）
> 节奏：Functional Design 阶段 = 本计划 + 3 文档（domain-entities + business-rules + business-logic-model），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 模块与 design_status 扩展
- [Answer] 新建 `modules/design/`；design_status 沿用 U02 Style.design_status（VARCHAR(16)，DB 字段不变，U02 Q11 决策），Python Enum 扩展为 7 态：设计中/制版中/工艺录入/待补全/待核价/大货/已取消。create_design 复用 U02 StyleService 创建 Style 后置 design_status="设计中"。

### Q2 — 子表设计
- [Answer] 3 业务子表（均 1:1 with style，TenantScopedModel + RLS）：style_fabric（面辅料 JSONB + 补齐字段）、style_pattern（pattern_no + pattern_file_key R2 private + grading_data JSONB）、style_craft（craft_info JSONB）。+ 1 历史表 design_workflow_log（状态变迁审计：from/to/action/driven_by/actor/reason/created_at）。

### Q3 — 自动核价（S09）
- [Answer] submit_costing 传 cost_breakdown（面料用量+辅料用量+工艺费，Decimal）→ 求和 = style 级成本 → 写入该 style 下所有 active SKU 的 cost_price（覆盖）；sku.cost_price 写入受 U09 字段写权限约束（设计助理需在 sku.cost_price writable_roles —— 注意：当前注册表 writable=admin/merchandiser/finance，设计助理不在 → 由 design 模块以系统口径写入，不走字段写校验，记 audit）。

### Q4 — 状态机基类
- [Answer] 复用 core/state_machine.py（StateMachine + TransitionRule），与 U04/U05 一致；DesignStateMachine 集中声明转移表 + actor_roles 约束 + 必填字段守卫。

### Q5 — 驳回回退映射（S06/S12）
- [Answer] reject 回退到"上一环节"：制版中→设计中、工艺录入→制版中、待补全→工艺录入、待核价→待补全；记 driven_by（version_maker/merchandiser/design_assistant）+ reason，通知上游角色；非法 from 状态 → 422。大货/已取消为终态不可 reject。

### Q6 — 通知
- [Answer] 复用 U07 NotificationService.notify(user_ids, content, link, type)；新增 NotificationType.DESIGN_ADVANCE / DESIGN_REJECT / DESIGN_DONE；按"下一环节角色"解析租户内该角色全部 user_id（新增 RoleRepository.list_user_ids_by_role_code）；通知与状态推进同事务。

### Q7 — 不推进的动作
- [Answer] submit_pattern（仅写 style_pattern 文件/版号，状态不变，停留制版中）、complete_fabric（待补全补齐，状态不变）、set_tag_price（待核价填吊牌价，状态不变，写 sku.tag_price）= 3 个"原地"动作，不触发状态推进/通知。

### Q8 — 取消（S13）
- [Answer] cancel：任意非终态 + 已取消幂等拒绝？→ 任意 design_status（含中间态）→ 已取消，不可逆；仅 admin；记 audit + design_workflow_log；已取消后任何推进/驳回 → 422。

### Q9 — 权限 scope
- [Answer] 复用 default_roles 既有 design.* scope：design.design:write（设计师 DESIGN_ALL 含）、design.pattern:read/write（版师）、design.craft:write（跟单）、design.costing:write（设计助理）、design.tag_price:write（跟单）、design.confirm_price:approve（跟单）；migration 013 补 seed 这些细分 scope（幂等，绑对应角色）。create_design 用 design.design:write。

### Q10 — 列表看板（S01）
- [Answer] GET /api/designs 按 design_status 分组计数 + 列表（复用 Style 查询 + design_status filter）；GET /api/designs/{id} 聚合返回 style + fabric/pattern/craft 子表 + 当前可用 actions（按角色+状态）。

---

## 2. 执行步骤

- [x] 2.1 `U10a/functional-design/domain-entities.md`：design_status 7 态 Enum + 3 子表（style_fabric/pattern/craft）+ design_workflow_log + 与 Style/Sku 关系 ER 图 + NotificationType 扩展 + 字段清单
- [x] 2.2 `U10a/functional-design/business-rules.md`：BR-U10a-01~ 状态机转移表 + actor_roles + 必填守卫 + 自动核价算法 + 驳回回退映射 + 取消不可逆 + 通知规则 + 权限矩阵 + 错误码
- [x] 2.3 `U10a/functional-design/business-logic-model.md`：13 故事 UC 流程（create_design/submit_fabric/pattern/grading/craft/complete_fabric/submit_costing/set_tag_price/confirm_price/reject/cancel/list/detail）+ J1 端到端时序 + 跨单元契约（U02 StyleService/Sku、U07 Notification、core attachment/state_machine）
- [x] 2.4 诊断器无警告 + 与 stories EP03-S02~S14 + application-design §8 一致

---

**等待用户"继续"；本轮直接生成 3 份功能设计文档。**
