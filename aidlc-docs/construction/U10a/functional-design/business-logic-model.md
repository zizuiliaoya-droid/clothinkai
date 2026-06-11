# U10a 业务逻辑模型（设计制版全流程）

> 单元：U10a — EP03-S02~S14；13 个 Use Case + J1 端到端时序 + 跨单元契约

---

## 1. 用例总览

| UC | 动作 | 端点 | 状态变化 | 故事 |
|---|---|---|---|---|
| UC-1 | create_design | POST /api/designs/ | →设计中 | S02 |
| UC-2 | submit_fabric | PUT /api/designs/{id}/fabric | 设计中→制版中 | S03 |
| UC-3 | submit_pattern | PUT /api/designs/{id}/pattern | 原地（制版中） | S04 |
| UC-4 | submit_grading | PUT /api/designs/{id}/grading | 制版中→工艺录入 | S05 |
| UC-5 | reject（版师） | PUT /api/designs/{id}/reject | 制版中→设计中 | S06 |
| UC-6 | submit_craft | PUT /api/designs/{id}/craft | 工艺录入→待补全 | S07 |
| UC-7 | complete_fabric | PUT /api/designs/{id}/fabric/complete | 原地（待补全） | S08 |
| UC-8 | submit_costing | PUT /api/designs/{id}/complete | 待补全→待核价 | S09 |
| UC-9 | set_tag_price | PUT /api/designs/{id}/tag-price | 原地（待核价） | S10 |
| UC-10 | confirm_price | PUT /api/designs/{id}/confirm-price | 待核价→大货 | S11 |
| UC-11 | reject（通用） | PUT /api/designs/{id}/reject | 回退上一环节 | S12 |
| UC-12 | cancel | PUT /api/designs/{id}/cancel | 任意→已取消 | S13 |
| UC-13 | list / detail | GET /api/designs[/{id}] | — | S01/S14 |

---

## 2. 核心用例流程

### UC-1 create_design（S02）
```
1. 校验 design.design:write 权限
2. style_code 唯一性 → 复用 StyleService.create_style（main_image → R2 public）
3. 置 design_status="设计中"
4. 写 design_workflow_log(from=∅, to=设计中, action=create)
5. commit → 返回 style 详情
异常：style_code 重复 → 409
```

### UC-2 submit_fabric（S03）
```
1. 校验权限 + 加载 style，断言 design_status="设计中"（否则 422）
2. fabrics 非空校验（缺 → 422）
3. upsert style_fabric(fabrics, accessories)
4. DesignStateMachine: 设计中 --submit_fabric--> 制版中
5. 写 design_workflow_log + 解析"版师"角色 user_ids → NotificationService.notify(DESIGN_ADVANCE)
6. commit
```

### UC-4 submit_grading（S05）
```
1. 权限 + 断言 design_status="制版中" + style_pattern 已存在（版型已上传）
2. 更新 style_pattern.grading_data
3. 制版中 --submit_grading--> 工艺录入
4. log + 通知"跟单"
```

### UC-8 submit_costing（S09，自动核价）
```
1. 权限(design.costing:write) + 断言 design_status="待补全"
2. 校验 cost_breakdown{fabric_cost,accessory_cost,craft_cost} 均 ≥0（否则 422）
3. total = fabric+accessory+craft
4. 写 style 下所有 active SKU.cost_price = total（系统口径，绕过 U09 字段写校验，audit 脱敏）
5. 待补全 --submit_costing--> 待核价
6. log + 通知"跟单"
```

### UC-11 reject（S12，通用驳回）
```
1. 权限 + 加载 style
2. 查驳回回退映射[current] → prev（无映射/终态 → 422）
3. reason 必填（缺 → 422）
4. design_status = prev，driven_by 按当前环节角色
5. 写 design_workflow_log(action=reject, reason) + audit
6. 通知上游角色（DESIGN_REJECT，content 附 reason）
```

### UC-12 cancel（S13）
```
1. 校验 admin（* 通配）
2. design_status 非终态（否则幂等/422）
3. reason 必填
4. design_status="已取消"（不可逆）
5. log(action=cancel) + audit
```

### UC-13 list / detail（S01/S14）
```
list:  GET /api/designs → 按 design_status 分组计数 + 分页列表（复用 Style 查询 + status filter）
detail: GET /api/designs/{id} → style + fabric/pattern/craft 子表 + workflow_log 时间线
        + available_actions（按当前角色 + design_status 计算可执行动作集）
unread: 复用 U07 GET /api/notifications/unread-count
```

---

## 3. J1 端到端时序（设计→大货）

```
设计师 create_design ──→ 设计中
设计师 submit_fabric ──→ 制版中  ⟶通知版师
版师   submit_pattern (原地，传版号+文件)
版师   submit_grading ──→ 工艺录入 ⟶通知跟单
跟单   submit_craft   ──→ 待补全  ⟶通知设计助理
设计助理 complete_fabric (原地补齐)
设计助理 submit_costing ──→ 待核价 ⟶通知跟单（自动核价写 SKU.cost_price）
跟单   set_tag_price (原地，写 SKU.tag_price)
跟单   confirm_price  ──→ 大货   ⟶通知设计师（DESIGN_DONE）
（任意中间环节）reject ──→ 回退上一环节 ⟶通知上游
（admin 任意时刻）cancel ──→ 已取消
```

---

## 4. 跨单元契约

| 依赖 | 复用 | 用途 |
|---|---|---|
| U02 StyleService / StyleRepository | create_style / get_by_id / 列表 | 创建/查询 style |
| U02 SkuRepository | list_by_style / 更新 cost_price/tag_price | 自动核价 + 吊牌价 |
| U07 NotificationService | notify / unread_count | 推进/驳回通知 |
| U07 NotificationType | 追加 DESIGN_* | 通知分类 |
| core/state_machine | StateMachine + TransitionRule | DesignStateMachine |
| core/attachment | R2 public（设计稿）/ private（版型文件） | 文件存储 |
| U01 RoleRepository | 新增 list_user_ids_by_role_code | 按角色解析通知目标 |
| U01 audit | AuditService.log | 驳回/取消/核价审计 |

---

## 5. 角色待办（available_actions 矩阵，detail 用）

| design_status | designer | pattern_maker | merchandiser | design_assistant | admin |
|---|---|---|---|---|---|
| 设计中 | submit_fabric | — | — | — | cancel |
| 制版中 | — | submit_pattern/submit_grading/reject | — | — | cancel |
| 工艺录入 | — | — | submit_craft/reject | — | cancel |
| 待补全 | — | — | reject | complete_fabric/submit_costing | cancel |
| 待核价 | — | — | set_tag_price/confirm_price/reject | — | cancel |
| 大货/已取消 | — | — | — | — | — |

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 覆盖 EP03-S02~S14（13 故事） | ✅ §1 |
| 与 application-design §8 DesignService 方法签名一致 | ✅ |
| 状态机 7 态 + 驳回回退 + 取消终态 | ✅ business-rules §1/§2/§6 |
| 自动核价 / 吊牌价 / 原地动作 | ✅ §UC-8/UC-9 |
| 复用 U02/U07/core，无新依赖 | ✅ §4 |
