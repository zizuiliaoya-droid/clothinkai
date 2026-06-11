# U10a 业务规则（设计制版全流程）

> 单元：U10a — EP03-S02~S14
> 状态机 7 态 + 驳回回退 + 取消不可逆 + 自动核价 + 角色通知

---

## 1. 状态机转移表（BR-U10a-01~20）

| from | action | to | actor 角色 | 必填守卫 | 通知对象 | 故事 |
|---|---|---|---|---|---|---|
| (无) | create_design | 设计中 | designer | style_code/style_name/image | — | S02 |
| 设计中 | submit_fabric | 制版中 | designer | fabrics 非空 | 版师(pattern_maker) | S03 |
| 制版中 | submit_pattern | 制版中（原地） | pattern_maker | pattern_no | — | S04 |
| 制版中 | submit_grading | 工艺录入 | pattern_maker | style_pattern 已存在 | 跟单(merchandiser) | S05 |
| 制版中 | reject | 设计中 | pattern_maker | reason | 设计师(designer) | S06 |
| 工艺录入 | submit_craft | 待补全 | merchandiser | craft_info | 设计助理(design_assistant) | S07 |
| 待补全 | complete_fabric | 待补全（原地） | design_assistant | — | — | S08 |
| 待补全 | submit_costing | 待核价 | design_assistant | cost_breakdown | 跟单 | S09 |
| 待核价 | set_tag_price | 待核价（原地） | merchandiser | tag_price>0 | — | S10 |
| 待核价 | confirm_price | 大货 | merchandiser | 吊牌价已填 | 设计师 | S11 |
| 中间任意 | reject | 上一环节 | 当前角色 | reason | 上游角色 | S12 |
| 任意非终态 | cancel | 已取消 | admin | reason | — | S13 |

- **BR-U10a-01**：非法 from→action 组合 → 422 ILLEGAL_STATE_TRANSITION。
- **BR-U10a-02**：终态（大货/已取消）不接受任何 action（除查询）→ 422。
- **BR-U10a-03**：actor 角色不匹配 action 允许角色 → 403（admin 通配可执行任意，但业务动作仍按状态机守卫）。

---

## 2. 驳回回退映射（BR-U10a-21~25，S06/S12）

| 当前状态 | reject → 回退到 | driven_by | 通知 |
|---|---|---|---|
| 制版中 | 设计中 | version_maker | 设计师 |
| 工艺录入 | 制版中 | merchandiser | 版师 |
| 待补全 | 工艺录入 | design_assistant | 跟单 |
| 待核价 | 待补全 | merchandiser | 设计助理 |

- **BR-U10a-21**：reject 必填 reason（缺 → 422）。
- **BR-U10a-22**：设计中（无上一环节）/ 大货 / 已取消 不可 reject → 422。
- **BR-U10a-23**：reject 写 design_workflow_log（from/to/action=reject/driven_by/reason）+ audit_log。

---

## 3. 自动核价（BR-U10a-30~34，S09）

- **BR-U10a-30**：submit_costing 传 cost_breakdown = {fabric_cost, accessory_cost, craft_cost}（Decimal ≥ 0）。
- **BR-U10a-31**：style 级总成本 = fabric_cost + accessory_cost + craft_cost。
- **BR-U10a-32**：写入该 style 下**所有 active SKU** 的 cost_price（覆盖）；以 design 模块系统口径写入，**不经 U09 字段写权限校验**（设计助理非 cost_price writable_roles，但核价是其法定职责）；写 audit（敏感值脱敏，仅记 cost_price_changed=true）。
- **BR-U10a-33**：分项金额缺失或为负 → 422。
- **BR-U10a-34**：无 active SKU 的 style 提交核价 → 仍推进状态（成本待后续 SKU 创建时不自动回填，记 warning）。

---

## 4. 吊牌价（BR-U10a-40，S10）

- **BR-U10a-40**：set_tag_price 写该 style 下所有 active SKU 的 tag_price；tag_price > 0；状态不变（停留待核价）。

---

## 5. 原地动作（BR-U10a-45~47）

- **BR-U10a-45**：submit_pattern 写 style_pattern（pattern_no + 文件 R2 private），状态停留制版中，不通知。
- **BR-U10a-46**：complete_fabric upsert style_fabric（is_completed=true），状态停留待补全，不通知。
- **BR-U10a-47**：set_tag_price 见 §4，不通知。

---

## 6. 取消（BR-U10a-50~52，S13）

- **BR-U10a-50**：cancel 仅 admin；任意非终态 design_status → 已取消，不可逆。
- **BR-U10a-51**：已取消 style 任何推进/驳回/原地动作 → 422。
- **BR-U10a-52**：cancel 必填 reason；写 design_workflow_log + audit。

---

## 7. 通知（BR-U10a-60~63，S14）

- **BR-U10a-60**：状态推进（非原地）成功后，按"通知对象角色"解析租户内该角色全部 active user_id，复用 NotificationService.notify 写站内通知（同事务）。
- **BR-U10a-61**：通知 type：推进=DESIGN_ADVANCE、驳回=DESIGN_REJECT、转大货=DESIGN_DONE；content 含 style_code + 环节 + （驳回）reason；link 指向设计详情。
- **BR-U10a-62**：通知与状态变更同事务（失败一起回滚，保证一致）；无目标用户（该角色租户内无人）→ 跳过不报错。
- **BR-U10a-63**：GET /api/notifications/unread-count 复用 U07（本单元不新增通知查询端点）。

---

## 8. 权限矩阵（BR-U10a-70~72）

| action | scope | 角色 |
|---|---|---|
| create_design / submit_fabric | design.design:write | designer |
| submit_pattern / submit_grading / reject(制版中) | design.pattern:write | pattern_maker |
| submit_craft / set_tag_price / confirm_price | design.craft:write / design.tag_price:write / design.confirm_price:approve | merchandiser |
| complete_fabric / submit_costing | design.costing:write | design_assistant |
| cancel | （admin 通配 *） | admin |
| list / detail | design.*:read | 全设计制版角色 + admin |

- **BR-U10a-70**：migration 013 seed 细分 design.* scope（design.design:write / design.pattern:read|write / design.craft:write / design.costing:write / design.tag_price:write / design.confirm_price:approve / design.design:read）+ 绑对应角色（幂等）。
- **BR-U10a-71**：admin（*）可执行任意动作（含取消），但仍受状态机合法性约束。

---

## 9. 错误码矩阵

| 场景 | code | HTTP |
|---|---|---|
| 非法状态转移 | ILLEGAL_STATE_TRANSITION | 422 |
| 必填字段缺失 | VALIDATION_ERROR | 422 |
| style_code 重复（create） | STYLE_CODE_CONFLICT | 409 |
| 无操作权限 | PERMISSION_DENIED | 403 |
| style 不存在 | STYLE_NOT_FOUND | 404 |
| 驳回缺 reason | VALIDATION_ERROR | 422 |
