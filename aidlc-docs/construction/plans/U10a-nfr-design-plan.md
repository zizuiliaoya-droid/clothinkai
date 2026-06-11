# U10a NFR 设计计划（NFR Design Plan）

> 单元：U10a — 设计制版全流程
> 范围：将 NFR Requirements 落地为可实现模式 + 组件清单
> 节奏：NFR Design 阶段 = 本计划 + 2 文档（nfr-design-patterns.md + logical-components.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 状态机与并发的协作
- [Answer] DesignStateMachine（core/state_machine 子类）负责**校验**（find rule + actor_roles + required_fields）；实际状态变更走 repository `update_design_status(style_id, from, to)` = UPDATE WHERE design_status=:from RETURNING（乐观并发，None→StateTransitionConflict 409）。与 U04/U05 一致：状态机校验 + DB 条件 UPDATE。

### Q2 — 模式数量
- [Answer] 3 模式：**P-U10a-01**（DesignStateMachine 转移表 + 校验 + 乐观并发推进 + 副作用编排：子表 upsert + workflow_log + notify 同事务）；**P-U10a-02**（自动核价求和 + bulk SKU cost_price 系统口径写入绕过 U09 + audit 脱敏）；**P-U10a-03**（driven_by 服务端推断 + 按角色解析 user_ids + NotificationService.notify 同事务）。

### Q3 — 原地动作处理
- [Answer] submit_pattern / complete_fabric / set_tag_price 不走状态机 transition（无状态变化），仅做子表 upsert / SKU.tag_price 写入 + 断言当前状态（设计中外的前置态校验）；不写 workflow_log（或记 action 但 from==to）、不通知。

### Q4 — driven_by 推断
- [Answer] reject 的 driven_by 由"当前 design_status"映射推断（制版中→version_maker / 工艺录入→merchandiser / 待补全→design_assistant / 待核价→merchandiser），不信任客户端；cancel→admin。

### Q5 — available_actions
- [Answer] domain.compute_available_actions(design_status, role_codes) 复用状态机 get_valid_actions + admin 通配补 cancel；detail 端点返回供前端渲染按钮。

### Q6 — 核价绕过 U09
- [Answer] submit_costing 直接 repository.bulk_update_cost_price（不经 SkuService._check_price_write_permission）；理由：核价是设计助理法定职责，design.costing:write 已授权；读 cost_price 仍受 U09 字段权限。

### Q7 — RoleRepository 扩展
- [Answer] 新增 `list_user_ids_by_role_code(role_code) -> list[UUID]`（join role+user_role，租户内；测试 bypass 显式 tenant 过滤）。

### Q8 — migration 013
- [Answer] 4 表（style_fabric/pattern/craft UNIQUE(style_id) + design_workflow_log idx(tenant,style,created_at)）+ RLS + FK CASCADE + design.* scope seed 绑角色（幂等）。接 012。

---

## 2. 执行步骤

- [x] 2.1 `U10a/nfr-design/nfr-design-patterns.md`：P-U10a-01（DesignStateMachine 转移表 + transition 校验 + update_design_status 乐观并发 + 副作用编排完整伪代码）+ P-U10a-02（自动核价 + bulk SKU update + audit）+ P-U10a-03（driven_by 推断 + 角色解析 notify 同事务）
- [x] 2.2 `U10a/nfr-design/logical-components.md`：modules/design 全文件清单 + 横切改动（wecom/enums + auth/repository + main）+ migration 013 + 依赖图 + 索引 + RLS + 5 测试文件 + 一致性校验
- [x] 2.3 诊断器无警告 + 与 nfr-requirements / functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 设计文档。**
