# U10a NFR 设计模式（NFR Design Patterns）

> 单元：U10a — 设计制版全流程
> 增量模式：P-U10a-01（状态机+乐观并发+副作用编排）、P-U10a-02（自动核价）、P-U10a-03（角色通知+driven_by 防伪）
> 继承：core/state_machine（U04/U05）、core/attachment（U02/U05）、U07 NotificationService、U01 audit

---

## P-U10a-01 — DesignStateMachine + 乐观并发 + 副作用编排

### 转移表（modules/design/state_machines.py）

```python
from app.core.state_machine import StateMachine, TransitionRule
from app.modules.design.enums import DesignStatus as DS

DESIGN_TRANSITIONS: tuple[TransitionRule, ...] = (
    TransitionRule(DS.DESIGNING, "submit_fabric", DS.PATTERNING,
                   actor_roles=("designer",), required_fields=("fabrics",),
                   side_effects=("upsert_fabric", "notify_pattern_maker")),
    TransitionRule(DS.PATTERNING, "submit_grading", DS.CRAFTING,
                   actor_roles=("pattern_maker",),
                   side_effects=("require_pattern", "notify_merchandiser")),
    TransitionRule(DS.CRAFTING, "submit_craft", DS.COMPLETING,
                   actor_roles=("merchandiser",), required_fields=("craft_info",),
                   side_effects=("upsert_craft", "notify_design_assistant")),
    TransitionRule(DS.COMPLETING, "submit_costing", DS.PRICING,
                   actor_roles=("design_assistant",), required_fields=("cost_breakdown",),
                   side_effects=("auto_costing", "notify_merchandiser")),
    TransitionRule(DS.PRICING, "confirm_price", DS.MASS_PRODUCTION,
                   actor_roles=("merchandiser",),
                   side_effects=("require_tag_price", "notify_designer_done")),
    # reject / cancel 单独处理（动态目标，见下）
)
```

### 状态推进（service，乐观并发 + 副作用同事务）

```python
async def _advance(self, style, action, *, user, payload):
    roles = await self._roles.list_codes_for_user(user.id)
    sm = DesignStateMachine(target=style, transition_table=DESIGN_TRANSITIONS,
                            state_attr="design_status")
    rule = sm._find_rule(action)            # 校验合法性（含 actor_roles / required_fields）
    if rule is None:
        raise IllegalStateTransitionError(...)
    sm.transition(action, actor_roles=roles, payload=payload)  # 校验 + 内存推进

    # DB 乐观并发推进（防并发双推）
    updated = await self._repo.update_design_status(
        style.id, from_status=rule.from_state, to_status=rule.to_state)
    if updated is None:
        raise StateTransitionConflictError("状态已变更，请刷新")  # 409

    # 副作用同事务：子表 upsert / 核价 / workflow_log / notify
    await self._run_side_effects(rule, style, user, payload)
    await self._repo.add_workflow_log(style.id, rule.from_state, rule.to_state,
                                      action=action, actor_id=user.id)
    await self._session.commit()
```

- 与 U04/U05 一致：状态机做**语义校验**，repository 条件 UPDATE 做**并发安全**。
- 全部副作用（子表 + 核价 + log + notify）在 commit 前，保证一致性。

### 原地动作（无状态变化，不经 transition）

```python
# submit_pattern / complete_fabric / set_tag_price
async def submit_pattern(self, style_id, payload, user):
    style = await self._require_style(style_id)
    self._assert_status(style, DS.PATTERNING)     # 前置态断言（否则 422）
    await self._repo.upsert_pattern(style_id, pattern_no=payload.pattern_no,
                                    pattern_file_key=key)
    await self._session.commit()                  # 不推进、不通知
```

### reject / cancel（动态目标）

```python
REJECT_PREVIOUS = {
    DS.PATTERNING: DS.DESIGNING,
    DS.CRAFTING: DS.PATTERNING,
    DS.COMPLETING: DS.CRAFTING,
    DS.PRICING: DS.COMPLETING,
}
DRIVEN_BY = {DS.PATTERNING: "version_maker", DS.CRAFTING: "merchandiser",
             DS.COMPLETING: "design_assistant", DS.PRICING: "merchandiser"}

async def reject(self, style_id, reason, user):
    style = await self._require_style(style_id)
    cur = style.design_status
    prev = REJECT_PREVIOUS.get(cur)
    if prev is None or not reason:           # 终态/设计中/缺 reason
        raise IllegalStateTransitionError(...)  # 或 ValidationError(reason)
    updated = await self._repo.update_design_status(style_id, cur, prev)
    if updated is None: raise StateTransitionConflictError(...)
    await self._repo.add_workflow_log(style_id, cur, prev, action="reject",
                                      driven_by=DRIVEN_BY[cur], actor_id=user.id, reason=reason)
    await self._notify_upstream(prev, style, reason)   # DESIGN_REJECT
    await self._audit.log("design.reject", resource="style", resource_id=style_id,
                          after={"from": cur, "to": prev, "reason_provided": True})
    await self._session.commit()

async def cancel(self, style_id, reason, user):   # admin only（require_permission *）
    style = await self._require_style(style_id)
    if style.design_status in (DS.MASS_PRODUCTION, DS.CANCELLED):
        raise IllegalStateTransitionError(...)   # 终态不可取消/幂等拒绝
    if not reason: raise ValidationError(...)
    await self._repo.update_design_status(style_id, style.design_status, DS.CANCELLED)
    await self._repo.add_workflow_log(..., action="cancel", driven_by="admin", reason=reason)
    await self._audit.log("design.cancel", ...)
    await self._session.commit()
```

---

## P-U10a-02 — 自动核价（系统口径写 SKU）

```python
async def _auto_costing(self, style, payload):
    cb = payload["cost_breakdown"]
    for k in ("fabric_cost", "accessory_cost", "craft_cost"):
        if cb.get(k) is None or cb[k] < 0:
            raise ValidationError(f"核价分项非法: {k}")
    total = cb["fabric_cost"] + cb["accessory_cost"] + cb["craft_cost"]   # Decimal
    # 系统口径：直接 bulk update，不经 SkuService 字段写权限（design.costing:write 已授权）
    n = await self._repo.bulk_update_sku_cost_price(style.id, total)
    await self._audit.log("design.auto_costing", resource="style", resource_id=style.id,
                          after={"cost_price_changed": True, "sku_count": n})  # 敏感值脱敏
```

- `bulk_update_sku_cost_price` = `UPDATE sku SET cost_price=:t WHERE style_id=:sid AND is_active AND tenant_id=:tid`。
- 读 cost_price 仍受 U09 字段权限（SkuService._to_response 过滤）。

---

## P-U10a-03 — driven_by 防伪 + 按角色通知（同事务）

```python
# 通知目标角色映射（推进）
NOTIFY_ROLE = {"submit_fabric": "pattern_maker", "submit_grading": "merchandiser",
               "submit_craft": "design_assistant", "submit_costing": "merchandiser",
               "confirm_price": "designer"}

async def _notify_next(self, action, style):
    role = NOTIFY_ROLE[action]
    user_ids = await self._roles.list_user_ids_by_role_code(role)   # 租户内 active
    if not user_ids:
        return                                  # 该角色无人 → 跳过不报错
    ntype = NotificationType.DESIGN_DONE if action == "confirm_price" else NotificationType.DESIGN_ADVANCE
    await self._notifier.notify(user_ids, content=f"款式 {style.style_code} 进入下一环节",
                                link=f"/designs/{style.id}", type=ntype.value)
```

- `driven_by` / 通知角色全部**服务端推断**（NOTIFY_ROLE / DRIVEN_BY 常量），不读客户端入参 → 防伪。
- notify 不自 commit，复用 `_advance` 的同一事务。

---

## 模式与 NFR 映射

| 模式 | NFR 目标 |
|---|---|
| P-U10a-01 | 单事务一致 + 乐观并发 409 + 性能 ≤300ms |
| P-U10a-02 | 自动核价同事务 + audit 脱敏 + 读仍受 U09 |
| P-U10a-03 | driven_by/通知防伪 + 角色解析 + 同事务 |

## 一致性校验

| 校验 | 结果 |
|---|---|
| 状态机校验 + DB 乐观并发推进 | ✅ P-U10a-01 |
| 原地动作不推进不通知 | ✅ P-U10a-01 |
| reject 回退映射 + driven_by 推断 | ✅ P-U10a-01/03 |
| cancel 终态不可逆 admin | ✅ P-U10a-01 |
| 自动核价绕过 U09 写 + 读受 U09 | ✅ P-U10a-02 |
| 通知同事务 + 角色解析 + 无人跳过 | ✅ P-U10a-03 |
