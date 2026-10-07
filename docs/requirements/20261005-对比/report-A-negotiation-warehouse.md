# 调研分支 A：谈款审核 → 仓库发货 → 推广单创建 → 催发（20261005 对照）

> 只读调研，基于 `main@a0f1410`（alembic head `056_goods_short_name`）。没有连生产；凡是要生产数据才能定的，标「需要生产核对」，只读 SQL 在 §5。
>
> 路径缩写：`N/` = `backend/app/modules/negotiation/`，`P/` = `backend/app/modules/promotion/`，`U/` = `backend/app/modules/urge/`，`AUTH/` = `backend/app/modules/auth/`，`SEC/` = `backend/app/core/security/`，`MIG/` = `backend/alembic/versions/`，`FE/` = `frontend/src/`。行号从 1 起。

## 1. 结论

图 5 要的「主管审 → 老板审 → 补收货地址 → 定稿后才生成推广单 → 人工复核推送仓库」现在只做了前半段：谈款只有 草稿 / 待审核 / 审核通过 / 审核驳回 四态，主管一通过就在同一事务里建推广单（N/service.py:321-370），没有老板审、全系统没有 typed 收货字段，也没有「推送仓库」这个动作——仓库队列靠推广单 `source_extra` 里的自由文本「打单地址 / 发货单号」驱动（P/repository.py:701-706）。催发主体已建好，口径与 10-02 一致；但「上传截图标记完成」前端完全没有入口，「按款式批量催发」的款式下拉请求 `page_size=200`、接口上限 100，按代码推断加载不出来。

计数（共 23 条）：**已满足 4 / 部分满足 9 / 缺失 8 / 冲突 2**。

最关键的三个设计决定都要业务拍板（§4）：主管驳回是不是终态（与批次 3 现状冲突）、老板退回退到哪一步、admin（= 老板，10-05 已确认）能不能代审主管一步。

## 2. 主表

| 编号 | 图·节点 | 需求（摘） | 现状（证据） | 判断 | 建议改动（后端 / 前端 / 迁移 / 权限） | 工作量 | 依赖 / 风险 |
|---|---|---|---|---|---|---|---|
| A-01 | 图5·PR新建谈款草稿 | PR 建草稿 | `create()` 落「草稿」N/service.py:141-180；页面「新建谈款」FE/pages/NegotiationPage.tsx | 已满足 | — | — | — |
| A-02 | 图5·PR填写博主、货号、报价 | 选博主、货号，填报价 | 字段齐（N/schemas.py:21-41）。博主下拉只拉前 100 个再本地过滤（FE/pages/NegotiationPage.tsx:118、463），后端其实支持 keyword（backend/app/modules/blogger/api.py:67-68）。款式下拉调 `GET /api/styles/`，要 `product:read`（backend/app/modules/product/api.py:106），而 pr / pr_manager 默认角色没有任何 product 读权限（AUTH/default_roles.py:141-189） | 部分满足 | 前端：博主下拉改成和款式一样的服务端搜索。权限：先跑 §5 S7；若确实没有，按 Q11 授权 | S | 款式 403 是按代码推断，生产账号是否另有授权未验证 |
| A-03 | 图5·PR提交单据 | 提交 | `submit()` N/service.py:255-280，写 `submitted_at`（:265） | 已满足 | 新流程里「老板退回」状态也能提交（A1 转移表） | — | — |
| A-04 | 图5·「谈款中，主管待审」及整条状态机 | 七个节点对应的状态 | 枚举四态 N/enums.py:18-21；CHECK `ck_negotiation_status` N/models.py:110-112、MIG/048_negotiation.py:101-104 | 部分满足 | 后端：状态改七态（§3 A1）；迁移：改 CHECK + 存量映射；前端：TABS / statusColor / 类型（FE/pages/NegotiationPage.tsx:60-73、FE/features/negotiation/types.ts） | M | `status` 是 `String(8)`（N/models.py:89），新值都 ≤6 字；库里 CHECK 实名带前缀 `ck_negotiation_ck_…`（backend/app/core/db.py:45） |
| A-05 | 图5·超36h高亮提醒主管 | 主管待审超 36h 高亮 | `submitted_at` 已有、每次提交重置、接口已返回（N/models.py:90、N/service.py:265、N/schemas.py:104），页面不显示也不计算 | 缺失 | 后端：行上返回 `is_overdue_36h` / `waiting_hours`（服务端算，不让前端拿阈值自己比——批次 4a 的教训）；`status-counts` 多一个超时计数（N/repository.py:205-218）。前端：行高亮 + 文字 Tag「已等 40h」（不只靠颜色）+ Tab 角标（已有 Badge NegotiationPage.tsx:395） | S | Q4：自然小时还是工作时间 |
| A-06 | 图5·主管审核（通过 → 待老板审核） | 主管通过不再直接建单 | `review()` 通过分支同事务 `create_promotion(autocommit=False)`（N/service.py:321-370）；权限 `negotiation.review:approve`（N/api.py:165）；自审禁止（N/service.py:297-301） | 部分满足 | 后端：通过 → 「待老板审核」，建单代码挪到 A-12；新增「持老板审权限的人不能做主管这一步」（Q3）。前端：通过提示语（NegotiationPage.tsx:175） | S | 测试 `_setup` 里 PR 与主管都用 admin_role（backend/tests/integration/test_negotiation.py:44-57），Q3 落地后要换成 `pr_role` / `pr_manager_role`（backend/tests/conftest.py:242、355） |
| A-07 | 图5·主管驳回 → 审批未通过，不生成推广单 | 驳回且不建单 | 「不建单」已满足（驳回分支不碰推广单 N/service.py:304-320，CHECK 兜底 N/models.py:118-123）。但现状驳回可改可重提（`_EDITABLE_STATUSES` N/service.py:65；改完回草稿 :232-234），图上这一支没有回边 | 冲突，需业务确认 | 推荐：驳回 = 终态（界面「审批未通过」），重谈用「复制为新谈款」（前端预填新建表单，不加接口）（Q1） | S | 存量「审核驳回」单会从可改变成只读（§5 S1 / S2 看量） |
| A-08 | 图5·博主卡片留存记录，展示驳回原因 | hover 卡显示驳回记录 | hover 卡两块数据都来自推广单 / 复盘（N/repository.py:130-180；FE/components/BloggerHoverCard/BloggerHoverCard.tsx:57-70）。重提会清空上一轮意见（N/service.py:266-269），驳回审计只记状态不记意见（:316）——意见一重提就永久丢失 | 缺失 | 迁移：新表 `negotiation_review_log`（只追加、RLS）；后端：history 响应加 `rejections`；前端：hover 卡加第三块「谈款驳回记录」 | M | 依赖 Q1；§3 A3 |
| A-09 | 图5·待老板审核 / 老板审核 | 第二级审核，老板 = admin（10-05） | 无此状态、无此动作；admin 持 `*`（AUTH/default_roles.py:93-104） | 缺失 | 权限：新 scope `negotiation.final_review:finalize`，只入册不绑角色（admin 靠 `*`，照 MIG/044 的做法）。后端：`final_review()` 定稿 / 退回，校验 `user ≠ pr_id`、`user ≠ reviewed_by`。迁移：`final_reviewed_by/at/opinion`。前端：「待老板审核」Tab + 定稿 / 退回按钮，显隐用后端给的 `can_finalize`（照 `can_refresh` 先例，`/me` 只有角色算不了 scope） | M | Q3；§3 A4 |
| A-10 | 图5·老板驳回退回 → 回到谈款中 | 退回 | 无 | 缺失 | 推荐退到 PR 可编辑的新状态「老板退回」（界面「谈款中 · 老板退回」），改完重提仍先主管后老板（Q2） | S | Q2 |
| A-11 | 图5·定稿 → 待PR补收货地址 → 录入收货信息 → PR确认提交地址 | 收货人 / 电话 / 地址 | 全系统没有 typed 收货字段：谈款表无（N/models.py）；推广单只有 `source_extra['打单地址']` 自由文本（FE/pages/PromotionListPage.tsx:84、P/repository.py:701-703）；博主只有 phone / wechat（backend/app/modules/blogger/models.py:59-60） | 缺失 | 迁移：谈款加 `receiver_name/phone/address` + `receiver_confirmed_at/by`。后端：`PUT …/receiver`（保存，仅「待补收货地址」可用）+ `POST …/confirm-receiver`（→ 已定稿 + 建单，A-12）；默认值取该博主最近一次已确认的收货信息。字段级权限见 §3 A6。前端：收货弹窗（保存 / 确认提交） | M | Q5；PII |
| A-12 | 图5·已定稿 → 自动生成推广记录 | 定稿后才建单 | 建单在主管通过时（N/service.py:338-351），带 8 个字段；`cooperation_date` = 建单当天（P/service.py:233） | 部分满足 | 后端：建单挪进 `confirm_receiver()`，同事务；多带 `receiver_*`（PromotionCreate 加可选字段）；先做条件 UPDATE（`WHERE status='待补收货地址'`）防并发重复建单；CHECK 改成「已定稿 ⇔ 有推广单」；测试改写（§3 A2） | S~M | 依赖 A-15 的推广单收件列先上 |
| A-13 | 图5·待发货：人工复盘 | 推仓前人工复核 | 没有「待发货」概念；PR 在「录入信息」里填了打单地址，单子就自动进仓库队列（FE/pages/WarehousePage.tsx:52-60） | 缺失 | 推广单加 `ship_pushed_at/by`；状态由时间戳推导：待推送 / 待打单 / 已发货。推广列表加「待推送」筛选和「确认推送仓库」按钮（新 scope `promotion.ship:push`）；推送前必须有收件三项 + 颜色及规格 | M | Q6 |
| A-14 | 图5·推送仓库打单 | 仓库接单打单 | 仓库页按 `has_print_address` / `has_waybill` 分桶（WarehousePage.tsx:52-60、P/repository.py:701-706），逐条手填单号。没有任何推广 / 仓库导出：现有只有报表导出（backend/app/modules/report/export_api.py:22-48）和导入失败 CSV（backend/app/modules/importer/api.py:163-180） | 部分满足 | 仓库页按推送状态分桶；加「导出待打单（xlsx）」（openpyxl 已在用 backend/app/modules/report/export_service.py:13；新 scope `promotion.warehouse:export` 显式授给仓库；带批次 6 水印） | S~M | Q7；批次 6 水印 / 导出权限 |
| A-15 | 图5 末 / 图4①·仓库回填快递单号 | 回填单号 | `PATCH /api/promotions/{id}/warehouse-waybill`（P/api.py:233-245，`promotion.warehouse:write`）只写 `source_extra['发货单号']`（P/service.py:634-654）；没有快递公司、没有发货时间、不校验单据状态；单号 1~128 字（P/schemas.py:152-153） | 部分满足 | 迁移：推广单加 `ship_courier / ship_waybill / shipped_at`，从 `source_extra` 回填并删键，MIG/032 的两个部分索引改建在 typed 列。后端：回填要求已推送、写 `shipped_at`；审计只记 changed 标记 | S~M | 存量见 §5 S9 |
| A-16 | 图4①·PR 端自动可见 | PR 能看到单号 | 能看到：推广列表把 `source_extra` 每个键铺成一列（PromotionListPage.tsx:793-801），「发货单号」在最右边一串列里。风险：「录入信息」弹窗整包覆盖 `source_extra`（:1679-1691，后端整字段替换 P/service.py:419-423）——弹窗开着时仓库回填的单号会被冲掉；清空输入框会删键（注释写「空值不覆盖」，代码是 `delete`，:1686） | 部分满足 | 推广列表前部加「发货」列（状态 Tag + 快递 + 单号可复制）+ 筛选；「打单地址 / 发货单号」移出 SOURCE_FIELDS（PromotionListPage.tsx:82-96），只走专用接口 | S | 依赖 A-15 |
| A-17 | 图4②·推广单创建，合作模式继承谈款且不可改 | 锁死 | 证据链见 §3 A8：谈款生成的单模式永不为空，「从空补一次」分支不可达；改成别的值 409 | 已满足 | 可选：补一条「谈款生成的单 PATCH 合作模式 → 409」集成测试 | S | — |
| A-18 | 图4②·推广单只从谈款来 | 两级审核不能被绕过 | 「新建推广」（P/api.py:66-79，`promotion:write`，PR 的 `promotion.*:*` 就能过）和「导入站外推广」（PR 持 `importer.batch:write`，AUTH/default_roles.py:153）都能不经谈款直接建单 | 部分满足 | 需业务确认（Q10）。推荐两个入口收窄到主管 + 管理员；新 scope 必须是独立一级域（如 `promotion_direct:create`），挂在 promotion 下会被 PR 的 `promotion.*:*` 命中 | S | 会改变 PR 现有能力；§5 S11 看最近直接建单量 |
| A-19 | 图4③·档期内 PR 跟进 | 档期内标签 | 推广列表「是否催发」列（PromotionListPage.tsx:709-720）由 `URGE_STATUS_SQL_EXPR` 算（P/urge_calculator.py:94-104），阈值读 `urge_config`（10 / 3） | 已满足 | — | — | — |
| A-20 | 图4④·是否超过约定发布时间 → 是 → 催发任务 | 过期才建任务 | 10-02 已定：排期前 ≤5 天自动建（`no_publish_days=5` U/models.py:42）；扫描窗口 `[今天−30, 今天+5]`（U/repository.py:381-418）；每天 08:30 北京（backend/app/core/celery_app.py:102-106） | 冲突，需业务确认 | 推荐保持 10-02（Q8）。若坚持图的字面：`no_publish_days` 要允许 0（CHECK U/models.py:78；前端 min=1 FE/pages/UrgePage.tsx:548） | S（仅改口径时） | — |
| A-21 | 图4⑤·催发任务：按款式批量筛选 | 按款式批量 | 批量催发后端在（U/service.py:295-360）、入口在（UrgePage.tsx:377、594-640），但款式下拉请求 `page_size: 200`（UrgePage.tsx:112），款式接口上限 100（backend/app/modules/product/api.py:112）→ 422，下拉为空；还受 A-02 的 product 读权限影响。任务列表接口支持 `style_id`（U/repository.py:311-313），页面没有款式筛选控件，只有关键字（能搜款号 :322-325） | 部分满足 | 前端：批量弹窗款式下拉改服务端搜索（照谈款页写法）；任务列表加款式筛选 + 「只看我的」（PRD 改动 2「我的催发待办」，接口已有 `pr_id`） | S | A-02 权限；未在浏览器复现 |
| A-22 | 图4⑤·上传截图标记完成 | 截图 + 完成 | 后端有带截图的催发（U/api.py:186-208）；前端 `urgePromotionWithScreenshot` 写好了但没有任何调用（FE/features/urge/api.ts:72）；任务页「催发」不带备注不带图（UrgePage.tsx:116-124、297）；推广页还提示「要附截图请到催发任务页」（PromotionListPage.tsx:1299-1300），那边并没有入口。没有「完成」动作：关闭原因只有 博主已发布 / 已取消 / 手动关闭（U/models.py:144-147）；关闭后连手动催都被拒（U/service.py:258） | 缺失 | 前端：催发弹窗加截图上传（接口现成）。后端 + 迁移：「标记完成」= 关闭原因新增「已完成」+ 截图必传 + 写一条留痕；手动催发遇到「已完成 / 手动关闭」的任务自动重开（自动扫描不重开） | M | Q9 |
| A-23 | 仓库页 / 推广列表「品名」（10-05 已定） | 改用商品简称 | WarehousePage.tsx:85、PromotionListPage.tsx:664 仍用 `style_short_name_snapshot` | 缺失（已定待实施） | 列表 CTE 已 JOIN goods_main（P/repository.py:618、633），多取 `short_name / goods_title`，按 `goods_display_name` 规则显示 | S | 可能与「商品简称」分支重复，汇总时去重 |

## 3. 逐条回答（A1 ~ A9）

### A1 谈款状态机

**现状**

- 状态：草稿 / 待审核 / 审核通过 / 审核驳回（N/enums.py:18-21）。
- 转移：草稿、审核驳回 —提交→ 待审核（N/service.py:255-280）；待审核 —通过→ 审核通过 + 同事务建推广单（:321-370）；待审核 —驳回→ 审核驳回（:304-320）；审核驳回 —编辑→ 草稿（:232-234）。只有草稿与审核驳回可改可提（:65）。
- 权限：读写用 `negotiation`，审核用 `negotiation.review:approve`（N/api.py:30-31、165）。授权矩阵 pr（read/write）、pr_manager（read/write/review）、finance（read）、operations（read）（MIG/048_negotiation.py:43-48；AUTH/default_roles.py:148-150、167-170、201-202、227）。
- CHECK 4 个（MIG/048_negotiation.py:101-116）：状态枚举、合作模式枚举、服务费 ≥0、`ck_negotiation_approved_has_promotion`（审核通过 ⇔ promotion_id 非空）。
- RLS：`enable_rls_sql("negotiation")`（MIG/048_negotiation.py:128），ENABLE + FORCE + `tenant_isolation`（SEC/rls.py:43-61）。
- 自审禁止：`pr_id == user.id` → 403（N/service.py:297-301）。
- 留痕：只有 audit_log（`negotiation.create/update/submit/review.*`），驳回只记状态不记意见（N/service.py:316），且 audit 读取要 `auth.audit:read`。PRD 模块一「所有状态变更必须记录操作人、时间」在页面上看不到。

**目标状态**（值都 ≤6 字，`status` 是 `String(8)`）

| 状态值 | 对应图上节点 | 谁能动 | 说明 |
|---|---|---|---|
| 草稿 | PR新建谈款草稿 | PR 编辑 / 提交 | 不变 |
| 主管待审 | 谈款中，主管待审 | 主管 | 原「待审核」改名；36h 从 `submitted_at` 起算 |
| 审核驳回 | 审批未通过 | — | 主管驳回。按 Q1 默认答案是终态，界面显示「审批未通过」 |
| 待老板审核 | 待老板审核 | admin | 新 |
| 老板退回 | 回到谈款中 | PR 编辑 / 提交 | 新，界面显示「谈款中 · 老板退回」（Q2） |
| 待补收货地址 | 待PR补收货地址 | PR 只能填收货信息 | 新 |
| 已定稿 | 已定稿 | — | 原「审核通过」改名；终态，有推广单 |

**转移表**

| 从 | 动作 | 到 | 接口权限 | service 规则 |
|---|---|---|---|---|
| — | 新建 | 草稿 | `negotiation:write` | 置换服务费压 0（现有） |
| 草稿 / 老板退回 | 编辑 | 不变 | `negotiation:write` | |
| 草稿 / 老板退回 | 提交 | 主管待审 | `negotiation:write` | 写 `submitted_at`；清本轮审核字段（历史进时间线表） |
| 主管待审 | 主管通过 | 待老板审核 | `negotiation.review:approve` | 审核人 ≠ PR；审核人不能持老板审权限（Q3） |
| 主管待审 | 主管驳回 | 审核驳回 | `negotiation.review:approve` | 意见必填（现有） |
| 待老板审核 | 老板定稿 | 待补收货地址 | `negotiation.final_review:finalize` | 审核人 ≠ PR 且 ≠ 主管审核人 |
| 待老板审核 | 老板退回 | 老板退回 | `negotiation.final_review:finalize` | 意见必填 |
| 待补收货地址 | 保存收货信息 | 不变 | `negotiation:write` + 字段写权限 | |
| 待补收货地址 | 确认提交地址 | 已定稿 | `negotiation:write` | 三项必填；条件 UPDATE + 同事务建推广单（A2） |
| 审核驳回 | 复制为新谈款 | 新单「草稿」 | `negotiation:write` | 仅 Q1 选终态时需要，前端预填即可 |

每次转移都往 `negotiation_review_log`（只追加：谁、何时、哪一级、什么动作、意见）写一行，补上 PRD 模块一的留痕要求，也是 A-08 驳回记录的数据源。

**scope 论证**（`has()` 在 SEC/permissions.py:39-53：持 `*` 全过；否则只认精确值，或 `第一段.*:*` / `第一段.*:<action>`）

- `negotiation.final_review:finalize` 只会被三种写法命中：`negotiation.final_review:finalize`、`negotiation.*:*`、`negotiation.*:finalize`。negotiation 域只入册过三条具体 scope（MIG/048_negotiation.py:35-39），从来没有 negotiation 通配；pr / pr_manager 手里是 `negotiation:read/write` 与 `negotiation.review:approve` 精确值；它们的 `promotion.*:*` 第一段不同，碰不到（已有单测 backend/tests/unit/test_negotiation_permissions.py:28-33）。
- action 刻意不用 `approve`：将来有人图省事给主管加一条 `negotiation.*:approve`，老板审也不会被一起放出去。和 `report.summary:refresh` 刻意不叫 `read` 是同一个做法。
- 自定义授权兜底：`grant` 只能授 permission 表里已有的 scope（AUTH/service.py:565-569），negotiation 通配从没入册，管理员在界面上也授不出去。生产有没有人被单独授过 `*`：§5 S6。
- 只有 admin / platform_admin 持 `*`（AUTH/default_roles.py:93-104）。platform_admin 也会命中，它是跨租户运维账号，风险低，不建议为此加判断。
- 保存 / 确认收货信息复用 `negotiation:write`，不新开 `negotiation.address:write`：PR 没有 negotiation 通配，新开就得显式补授，多一份迁移，又挡不住任何人。
- 单测照 test_negotiation_permissions.py 加一组：除 admin / platform_admin 外的默认角色 `has("negotiation.final_review", "finalize")` 全为 False；`negotiation.*:approve` 不放行；`negotiation.*:*` 会放行（反证，说明默认角色为什么不能给通配）。

**存量映射**（数量：§5 S1、S2）

| 现值 | 新值 | 影响 |
|---|---|---|
| 草稿 | 草稿 | 无 |
| 待审核 | 主管待审 | 上线后要多过一道老板审；`submitted_at` 已有，36h 立即生效 |
| 审核驳回 | 审核驳回 | 按 Q1 默认答案从「可改可重提」变成只读，PR 改用「复制为新谈款」 |
| 审核通过 | 已定稿 | 都已有推广单，CHECK 的不变式保持；收货信息为空，历史单不回溯要求 |

迁移顺序必须是：先删两个旧 CHECK → UPDATE 状态值 → 建新 CHECK（旧状态 CHECK 不认新值，旧 approved CHECK 引用的是「审核通过」）。

### A2 推广单生成时机

**现状**：`NegotiationService.review()` 的通过分支——取谈款 PR 的 User（N/service.py:329）→ `PromotionService(self._session).create_promotion(PromotionCreate(...), pr_user, autocommit=False)`（:338-351）→ 改「审核通过」+ 写 `promotion_id`（:353-357）→ 审计 → 统一 commit（:370）。`autocommit=False` 时 `create_promotion` 只 flush + 审计不提交（P/service.py:173-174、321-322）。

带过去的字段：款式、商品、博主、合作模式、平台、约定发布时间、服务费、备注（N/service.py:339-348）。没带：sku、笔记标题、`source_extra`（含颜色及规格）、寄回运费。服务端自己定的：建单人 = 谈款 PR（P/service.py:274）、合作日期 = 当天（:233，内部编码日期段跟着走）、样品成本按模式初始化。

**改到「已定稿」之后要动的地方**

1. service：`review()` 的通过分支只改状态；建单代码整段挪进新的 `confirm_receiver()`。
2. 并发：现在的 `review()` 没有行锁也没有条件更新（`session.get` 读、ORM 赋值写）。两个人同时点通过会各建一张推广单，后提交的覆盖 `promotion_id`，前一张成了没人认领的孤儿单——CHECK 照样满足，不会报错。新方法第一步用 `UPDATE negotiation SET status='已定稿' … WHERE id=:id AND status='待补收货地址' RETURNING id`，0 行就 409，拿到行再建单，同事务提交（与 `PromotionRepository.update_state` 的乐观并发同一个模式，P/repository.py:397）。
3. CHECK：`ck_negotiation_approved_has_promotion` 把「审核通过」换成「已定稿」，形状不变。
4. 事务：仍是 `autocommit=False` + 调用方统一 commit；收件信息通过 PromotionCreate 新增的可选字段一起写，不另起事务。
5. 字段：现有 8 个之外加 `receiver_name/phone/address`。颜色及规格不在谈款里采（图 5「录入收货信息」没提），放到 A-13 推送前复核。
6. 副作用：新单的合作日期从「主管通过日」后移到「确认地址日」，只影响新数据，报表口径不变。
7. 测试：test_negotiation.py 里通过即建单、审核通过是终态、驳回后改回草稿、hover 历史来自推广单、待审核排前面这几条都依赖旧状态值或「通过即建单」，要按三人三步改写。新增：建单抛错时谈款停在「待补收货地址」且没有推广单（故障注入）；并发确认只建一张（要两条独立连接，台账「先删后插不能并发」那条坑说明了单连接测不出来）。`promotion_factory` 要补新列 kwarg（台账：工厂静默忽略不认识的 kwarg）。

**收货地址要不要带进推广单**：要。仓库、推广列表、导出都以推广单为单位（WarehousePage.tsx:52-60），手工建 / 导入的推广单也要有地址。生成时复制进推广单 typed 列，之后以推广单为准（发货前改地址改推广单），谈款单上那份留作「定稿时确认的值」。

### A3 主管驳回

**现状**：驳回后能改（N/service.py:65），一改就回草稿（:232-234），能重新提交；重提时清空 `reviewed_by / reviewed_at / review_opinion`（:266-269）。驳回的审计只记状态（:316），所以意见在重提后彻底没了。前端驳回成功提示「PR 可以修改后重新提交」（NegotiationPage.tsx:188）。

**两种理解**

| | (1) 驳回即终态 | (2) 驳回可改重提（现状） |
|---|---|---|
| 状态机 | 「审核驳回」移出可编辑集合，界面叫「审批未通过」 | 不动 |
| 重谈方式 | 「复制为新谈款」：前端用旧单预填新建表单，不加接口 | 编辑后重提 |
| 驳回记录从哪来 | 直接查 `negotiation WHERE status='审核驳回'`，意见永远在行上 | 只能靠时间线表，每轮驳回一行 |
| 额外工作 | 复制按钮（S） | 时间线表是硬依赖 |

推荐 (1)。图上老板驳回明确画了「回到谈款中」，主管驳回那一支止于「审批未通过」，不对称应该是有意的；「博主卡片留存记录」也说明驳回是这次合作的定论，供下次选博主参考，可以反复改的中间态不适合进档案。两种都需要时间线表（老板退回那条循环也要留痕），所以 (1) 省不掉迁移，只是让卡片查询简单。

**博主卡片要加什么**

- 现状数据源：`GET /api/negotiations/blogger/{id}/history`（N/api.py:101-118，`negotiation:read`）只查推广单（N/repository.py:130-180）；复盘另走 `GET /api/bloggers/{id}/retrospectives`。卡片用在博主列表、谈款、催发三个页面。
- 接口：history 响应加 `rejections: [{negotiation_id, style_code, style_name, submitted_at, rejected_at, reviewer_name, opinion, quote_amount}]`，默认最近 5 条；`quote_amount` 按 `can_read_field("promotion", "quote_amount")` 过滤，与现有一致。只放主管驳回（终态）；老板退回是中间态，不上卡片，在谈款详情的时间线里看。
- 前端：BloggerHoverCard 在「历史复盘」后加「谈款驳回记录」（时间 + 款号 + 审核人 + 意见），沿用 `enabled: open` 懒加载。可选：新建谈款选中博主时，若有驳回记录，表单里给一行提示。

### A4 老板审核（老板 = admin，业务方 10-05）

- 角色：沿用 admin。`negotiation.final_review:finalize` 只入册不绑角色——admin 的 `*` 已覆盖；入册是为了以后老板不用 admin 账号时能单独授予（照 MIG/044 只入册不绑的做法）。
- 不会被现有通配拿到：论证见 A1。
- **admin 能否代审主管一步**：现状能（`*` 能过 `negotiation.review:approve`，前端 `canReview` 也把 admin 算进去 NegotiationPage.tsx:87-89）。推荐**不能**：主管审核接口注入 `CurrentPerms`，持老板审权限（`has("negotiation.final_review", "finalize")`）的人拒绝做主管这一步；前端主管按钮只给有主管权限且没有老板权限的人。理由：老板就是 admin，若 admin 代审了主管一步，再要求「两级不同人」这单就没人能定稿；若允许同一人过两级，留痕上是两道审批，实际是一个人的决定。逃生口：主管请假时，老板用现有自定义授权临时把 `negotiation.review:approve` 授给别人（AUTH/service.py:600-618，有审计）。
- **同一人能否过两级**：不能。老板审校验 `user ≠ reviewed_by`（新异常，403，形状同 `NegotiationSelfReviewForbiddenError`），同时校验 `user ≠ pr_id`。有了上一条，默认角色下这条不会触发，留作自定义授权场景的兜底。
- 前提：生产至少要有一个不是 admin 的 pr_manager 在用（§5 S4 / S5）。如果实际一直是 admin 在做主管审核，这条推荐会把流程卡死，改用备选「老板越级直批」：主管待审时 admin 可直接定稿，主管审核人留空、时间线记「跳过主管」。
- 已知边界：admin 自己建的谈款（admin 持 `negotiation:write`）走到老板审会被自审规则挡住，只有第二个 admin 能定稿。推荐接受（老板不会亲自录谈款），写进说明。
- **老板退回去向**：(a) 回到主管待审——主管只能「再批一次」（老板已否）或「驳回」（按 Q1 是终态），PR 没机会改条件；(b) 退回 PR 可编辑（「老板退回」），改完重提重新走主管 → 老板。推荐 (b)：老板退回通常是要再谈价格，要 PR 改条件，条件变了主管理应重看。
- **自审规则怎么延伸**：主管步 `user ≠ pr_id`（现有）+ 不持老板审权限；老板步 `user ≠ pr_id` 且 `user ≠ reviewed_by`。推广单结款审核（`SelfReviewForbiddenError` P/service.py:1060）、复盘确认不受影响。
- 前端显隐：`canReview` 现在按角色码硬编码（NegotiationPage.tsx:87-89），建议加一个 `GET /api/negotiations/capabilities` 返回 `can_write / can_review / can_finalize`，由后端按真实权限算。

### A5 超 36h 高亮提醒主管

- 字段：`submitted_at` 已有（N/models.py:90），每次提交刷新（N/service.py:265），存量待审单都有值。不用推断。
- 推荐「列表高亮 + 待办计数」，不推送：生产企微没配置（台账 4a：`wecom_config` 0 行）；后端虽有站内通知（backend/app/modules/wecom/notification_api.py:14），前端没有任何消费方，推了也没人看得到。
- 实现：服务端算 `is_overdue_36h`（`status='主管待审' AND now() - submitted_at > 36h`）和 `waiting_hours`；status-counts 多一个计数给 Tab 角标；「主管待审」Tab 内按 `submitted_at` 升序。阈值是后端常量，随响应下发，前端不写死。
- 36h 按自然小时还是工作时间 → Q4，推荐自然小时：图上写的就是 36h，工作日历还要维护节假日。周五下午提交的单周一早上会亮，正是要提醒的。
- 可选：「待老板审核」也显示已等小时数，但不高亮（图上没要求）。

### A6 收货地址

- **现状**（全库搜 收货 / 地址 / receiver / phone）：谈款表没有；推广单没有 typed 字段，只有 `source_extra['打单地址']` 自由文本（前端字段 PromotionListPage.tsx:84，仓库筛选 P/repository.py:701-703，部分索引 MIG/032_promotion_warehouse_index.py:30-31）；博主有 `phone` / `wechat`（backend/app/modules/blogger/models.py:59-60），字段级权限只给 admin / pr / pr_manager（SEC/field_permissions.py:43-56）；任何地方都没有「收货人」。
- **现在的泄露面**：推广响应把 `source_extra` 原样返回（P/service.py:2039），运营持 `promotion.*:read` 能读推广列表，于是能看到所有「打单地址」（通常含手机号）。
- **推荐存法**：谈款单录入 `receiver_name/phone/address`，生成推广单时复制进推广单同名 typed 列，之后以推广单为准。不在博主表加地址列，默认值取该博主最近一次已确认的收货信息（谈款或推广单），少一份会漂移的副本。存量「打单地址」整段回填进 `receiver_address`，姓名 / 电话留空（§5 S9 先看量和格式）。
- **字段级权限**（照 FIELD_PERMISSION_REGISTRY 现有写法，SEC/field_permissions.py:32-71）：
  - `negotiation.receiver_name / receiver_phone / receiver_address`：可见、可写都是 {admin, pr, pr_manager}。财务、运营虽有 `negotiation:read`，拿到的是 None。
  - `promotion.receiver_name / receiver_phone / receiver_address`：可见 {admin, pr, pr_manager, warehouse}，可写 {admin, pr, pr_manager}。
  - 超管照例全可见（SEC/field_permissions.py:91）。响应沿用「置 None」投影；谈款的 `_row_to_response`（N/service.py:131）现在只处理报价，要扩。
  - 审计只记 `receiver_changed: true`，不记值（同 blogger.phone，backend/app/modules/blogger/domain.py:22-28）。
  - 博主 phone 原本仓库看不到；预填到收件电话后仓库能看到。这是打单所需的有意放开，写进说明。
- 「打单地址」移出 SOURCE_FIELDS，迁移里删掉 JSONB 键（047 删「寄回单号」副本的同一做法，MIG/047_mode_flow_fields.py:64），运营那条泄露随之关闭。

### A7 仓库发货链路

**现状**

- 页面：仓库角色登录后只能进 `/warehouse-orders`（FE/App.tsx:62-74）。列表调推广列表接口，带 `has_print_address=true` + `has_waybill`（WarehousePage.tsx:52-60）。列：内部编码、货号、品名（款式简称快照）、颜色及规格、打单地址、发货单号、打单状态（看单号空不空推导）、操作（:83-115）。回填弹窗只有一个单号输入框。
- 接口：`PATCH /api/promotions/{id}/warehouse-waybill`，`promotion.warehouse:write`（P/api.py:233-245；MIG/031 把仓库收紧到只剩这一条写权限）。service 只写 `source_extra['发货单号']` + 审计 `waybill_changed`，不校验单据状态（P/service.py:634-654）。
- 字段：快递公司 ✗；单号是 JSONB 自由文本；发货状态 ✗（前端推导）；发货时间 ✗。
- PR 可见性：见 A-16，能看到，但会被「录入信息」整包覆盖冲掉。
- 「推送」：没有显式动作，PR 在「录入信息」里填了打单地址就等于推给了仓库。
- 导出：没有推广 / 仓库导出。现有只有报表导出（backend/app/modules/report/export_api.py:22-48，`report.export:read`）和导入失败明细 CSV（backend/app/modules/importer/api.py:163-180）。

**推荐**

- 推广单 typed 列：`receiver_*`（A6）、`ship_pushed_at/by`、`ship_courier`、`ship_waybill`、`shipped_at`。状态由时间戳推导（待推送 / 待打单 / 已发货），不再加一套状态机。
- 人工复盘：推广列表「待推送」筛选下，PR（主管也可）点「确认推送仓库」，校验收件三项 + 颜色及规格非空，写 `ship_pushed_at`。scope `promotion.ship:push`：PR / 主管的 `promotion.*:*` 覆盖；运营 `promotion.*:read` 的 action 对不上；仓库只有 `promotion:read` + `promotion.warehouse:write` 两条精确值，也对不上。
- 仓库页：分桶改成 待打单 / 已发货 / 全部；回填弹窗加快递公司下拉；后端回填要求已推送、写 `shipped_at`；已发货后收件信息只读。
- 导出打单表：推荐做（Q7）。xlsx 列：内部编码、收件人、电话、地址、货号、品名（商品简称）、颜色及规格。scope `promotion.warehouse:export` 显式授给仓库（PR / 主管经通配也能导，运营不能）；水印按批次 6 的定稿。批量导入回填单号先不做。

### A8 合作模式继承且不可改

结论：**已满足**。证据链：

1. 谈款的合作模式 NOT NULL + CHECK（N/models.py:72；MIG/048_negotiation.py:105-108）。
2. 生成时原样传入（N/service.py:343）→ 推广单落库（P/service.py:279）。
3. `update_promotion` 只在原值为空时允许补一次（P/service.py:366），值不同 409 `COOPERATION_MODE_IMMUTABLE`（:376；P/exceptions.py:103-110），值相同是空操作（应用变更时跳过该字段 :419-422）。谈款生成的单模式永不为空，「从空补一次」分支对它们不可达。
4. 其余写入路径：Excel 导入只 INSERT 新行且不带模式（backend/app/modules/importer/adapters/promotion.py:40、161-205），改不到已有单；前端只在新建表单出现模式选择（PromotionListPage.tsx:99、1062-1075）。
5. 已有通用测试 backend/tests/integration/test_promotion_crud.py:979、1039；可再补一条谈款来源的。

但图上「推广单创建，继承谈款」还有一个前提没守住：推广单可以不经谈款直接建，见 A-18 / Q10。

### A9 催发

- **现有规则**（与 10-02 一致）：`urge_config` 单租户单行，默认 临期 5 天 / 催 3 次提示主管 / 超时 30 天停自动 / 标签 10 天、3 天 / 自动开（U/models.py:42-74；缺行回退 U/service.py:69-76）。扫描每天 00:30 UTC = 08:30 北京（backend/app/core/celery_app.py:102-106）：先收口陈旧任务，再取排期在 `[今天−30, 今天+5]` 且未发布 / 异常的单（U/repository.py:381-418），一单一任务（`ON CONFLICT DO NOTHING` U/repository.py:135），当天只自动计一次。列表标签按 10 / 3 算（P/urge_calculator.py:94-104）。
- **与图的关系**：图「超过约定发布时间 → 是 → 催发任务」比 10-02 晚 5 天才开始，按约束写成冲突（Q8）。推荐保持 10-02：它的窗口已经包含「过期 30 天以内」，图上的情形全覆盖，只是多了提前 5 天的提醒；PRD 改动 2 原文也是 ≤5 天。
- **按款式批量**：后端 `urge_batch`（U/service.py:295-360，取该款式全部未发布 / 异常单，不收截图）在，入口也在（UrgePage.tsx:377、594-640），但款式下拉请求 `page_size=200`（:112）超过接口上限 100（backend/app/modules/product/api.py:112），会 422，下拉为空——功能现在实际不可用（按代码推断，未在浏览器复现）。PR / 主管能不能读款式列表还取决于 A-02 的权限问题。任务列表的款式筛选只有后端（U/repository.py:311-313），页面没有控件；关键字能搜到款号（:322-325）。
- **截图留痕**：后端有 `urge-with-screenshot`（U/api.py:186-208，后端代传 + 失败补偿删对象），时间线能显示截图（UrgePage.tsx:510-517）。前端没有任何上传入口：`urgePromotionWithScreenshot`（FE/features/urge/api.ts:72）没有调用方，任务页「催发」按钮不带备注也不带图（UrgePage.tsx:116-124、297）；推广页催发弹窗写着「要附聊天截图请到催发任务页操作」（PromotionListPage.tsx:1299-1300），那里并没有。
- **「标记完成」对应什么**：没有对应动作。任务只会因发布 / 取消自动关闭（P/service.py:841、895 → U/service.py:402-424）、扫描收口陈旧任务、或手动关闭（U/service.py:362-400：原因可选、不带图、原因固定「手动关闭」；CHECK 只允许 博主已发布 / 已取消 / 手动关闭，U/models.py:144-147）。关闭后再催会被拒（U/service.py:258；计次要求「进行中」U/repository.py:179），手动关闭的文案也定位成「不再催了」（UrgePage.tsx:174-197）。
- **推荐**（Q9）：「标记完成」= 关闭原因新增「已完成」+ 截图必传 + 写一条留痕；之后博主仍未发的，手动催发自动重开该任务（自动扫描不重开，免得每天早上把「已完成」翻回来）。日常催发截图保持可选，批量催发仍不收图（现有设计理由成立）。看板「待催发」= 进行中，口径不变。

## 4. 需业务确认

| # | 问题 | 推荐默认答案 | 理由 |
|---|---|---|---|
| Q1 | 主管驳回是不是终态？（与批次 3 现状「驳回可改后重提」冲突） | 是。界面显示「审批未通过」，要重谈点「复制为新谈款」 | 图上只有老板驳回画了「回到谈款中」，主管驳回止于「审批未通过」；「博主卡片留存记录」说明驳回是定论 |
| Q2 | 老板「驳回退回 → 回到谈款中」退到哪？ | 退回 PR 可编辑（新状态「老板退回」），改完重提仍先主管再老板 | 退回一般是要 PR 重谈条件；退到主管那里，PR 什么都改不了 |
| Q3 | admin（老板）能否代审主管一步？同一人能否既过主管审又过老板审？ | 都不能；主管请假时老板临时授权别人代主管审。前提：生产至少一个非 admin 的 pr_manager（§5 S4 / S5），否则改用「老板越级直批（记为跳过主管）」 | 老板 = admin 且只有一位时，代审后要么卡死，要么两级退化成一人 |
| Q4 | 36h 按自然小时还是工作时间？要不要推送？ | 自然小时；只做列表高亮 + Tab 计数，不推送 | 图写的就是 36h；企微未配置，站内通知没有前端 |
| Q5 | 收货信息存哪？默认值从哪来？ | 谈款单录入，生成推广单时复制、之后以推广单为准；默认值取该博主最近一次确认的收货信息，不在博主档案加列 | 仓库按推广单工作；少一份会漂移的副本 |
| Q6 | 「待发货：人工复盘」谁做？推送前必须有什么？ | 对接 PR（主管也可）点「确认推送仓库」；必须有收件三项 + 颜色及规格 | 谈款里没有颜色尺码，仓库缺这个发不了货 |
| Q7 | 仓库要不要导出打单表？要不要批量回填单号？ | 导出要做（xlsx，权限单独授仓库，带水印）；批量回填先不做 | 仓库通常用快递软件批量打单；回填量不大时逐条够用 |
| Q8 | 催发从什么时候开始：图上「过了约定发布时间」，还是 10-02 的「排期前 ≤5 天」？ | 保持 10-02 | 10-02 的窗口已覆盖过期 30 天内的情形，且是 PRD 改动 2 原文 |
| Q9 | 「上传截图标记完成」指什么？ | 新增「标记完成」（截图必传，可被之后的手动催发重开）；日常催发截图保持可选 | 图上该节点是红框必传；批量催发天然带不了逐条截图 |
| Q10 | 推广单是否只能从谈款生成？ | 「新建推广」「导入站外推广」收窄到主管 + 管理员（补录历史用），PR 一律走谈款（§5 S11 先看最近直接建单量） | 否则两级审核能被绕过。会改变 PR 现有能力 |
| Q11 | （取决于 §5 S7）PR / 主管若确实读不了款式列表，怎么授权？ | 款式接口改成 `require_permission("product.style", "read")`，给 pr / pr_manager 显式授 `product.style:read` | 现持 `product.*:*` / `product.*:read` 的跟单、设计、运营不受影响；不直接给 PR `product.*:read`，那会连带放开整个 product 域的读接口 |

## 5. 迁移与存量数据影响

按建议实施顺序编号（§6），三个迁移：

**057_promotion_shipping**（推广单收件 / 发货 typed 化）

- 加列：`receiver_name varchar(32)`、`receiver_phone varchar(32)`、`receiver_address varchar(255)`、`ship_pushed_at timestamptz`、`ship_pushed_by uuid`（FK user，SET NULL）、`ship_courier varchar(16)`、`ship_waybill varchar(128)`、`shipped_at timestamptz`。
- 回填：`source_extra->>'打单地址'` → `receiver_address`；`'发货单号'` → `ship_waybill`。有地址的存量单 `ship_pushed_at = updated_at`，保住今天的仓库队列；有单号的 `shipped_at = updated_at`（近似值，迁移注释写明）。
- 删 JSONB 键「打单地址」「发货单号」，下行迁移写回（同 047 的做法）。
- MIG/032 的两个部分索引删掉，按 typed 列重建（谓词要与列表 SQL 逐字一致，032 注释里写过这条）。
- **不清 `report_summary_coverage`**：汇总表不含这些列，报表也不读它们（054 的规则针对汇总表的列和口径）。
- 连带：`list_with_cte` 从 `__table__.columns` 反推列，自动带上（台账 4b-1）；`promotion_factory` 要补 kwarg；前端 SOURCE_FIELDS 去掉两项。

**058_negotiation_two_level**（谈款两级审核 + 收货信息 + 时间线）

- 加列：`final_reviewed_by`（FK user，SET NULL）、`final_reviewed_at`、`final_review_opinion`、`receiver_name/phone/address`、`receiver_confirmed_at`、`receiver_confirmed_by`。
- 删 2 个 CHECK → UPDATE（待审核 → 主管待审，审核通过 → 已定稿）→ 建新 CHECK。收货信息必填只在 service 校验：存量「已定稿」单没有地址，加 DB 约束会把它们卡住（与品名截图门槛放 service 不放 CHECK 同理）。
- 新表 `negotiation_review_log`：RLS FORCE；索引 `(tenant_id, negotiation_id, created_at DESC)`；层级 / 动作 CHECK；只追加，无 is_active。用现有行字段回填一轮（已提交的写「提交」，有审核人的写「通过 / 驳回 + 意见」）。更早轮次的意见已被清空，无法恢复；audit_log 只有动作和时间，没有意见。
- 权限入册 `negotiation.final_review:finalize`，不绑角色。
- 下行迁移有损：待老板审核 / 待补收货地址 → 待审核，老板退回 → 草稿，已定稿 → 审核通过；老板审与收货信息丢弃。
- 不涉及汇总表（报表模块不读 negotiation，已 grep 确认）。

**059_urge_complete**（视 Q9 / Q8）

- `ck_urge_task_close_reason` 加「已完成」。
- 仅当 Q8 选图的字面口径：`ck_urge_config_no_publish_days` 放宽到 `BETWEEN 0 AND 60`。

**需要生产核对的只读 SQL**（S9 刻意不输出地址原文，避免带出手机号）

```sql
-- S1 谈款状态分布（A1 存量映射）
SELECT status, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE promotion_id IS NOT NULL) AS with_promotion,
       COUNT(*) FILTER (WHERE review_opinion IS NOT NULL) AS with_opinion,
       MIN(created_at) AS first_at, MAX(created_at) AS last_at
FROM negotiation GROUP BY status ORDER BY status;

-- S2 驳回后重提过的单（Q1 两种理解的实际影响）
SELECT resource_id,
       COUNT(*) FILTER (WHERE action = 'negotiation.submit') AS submits,
       COUNT(*) FILTER (WHERE action = 'negotiation.review.reject') AS rejects,
       COUNT(*) FILTER (WHERE action = 'negotiation.review.approve') AS approves
FROM audit_log
WHERE resource = 'negotiation'
GROUP BY resource_id
HAVING COUNT(*) FILTER (WHERE action = 'negotiation.review.reject') > 0;

-- S3 现在待审超 36h 的量（A5）
SELECT COUNT(*) AS pending,
       COUNT(*) FILTER (WHERE now() - submitted_at > interval '36 hours') AS over_36h,
       MAX(now() - submitted_at) AS longest_wait
FROM negotiation WHERE status = '待审核';

-- S4 已有的主管审核是谁做的（admin 是否一直在代审，Q3）
SELECT r.code AS reviewer_role, COUNT(DISTINCT n.id) AS n
FROM negotiation n
JOIN user_role ur ON ur.user_id = n.reviewed_by
JOIN role r ON r.id = ur.role_id
WHERE n.reviewed_by IS NOT NULL
GROUP BY r.code;

-- S5 admin / pr_manager / pr 在用账号数，以及 admin 兼 pr_manager 的人数（Q3 前提）
SELECT COUNT(DISTINCT u.id) FILTER (WHERE r.code = 'admin')      AS admins,
       COUNT(DISTINCT u.id) FILTER (WHERE r.code = 'pr_manager') AS pr_managers,
       COUNT(DISTINCT u.id) FILTER (WHERE r.code = 'pr')         AS prs
FROM "user" u
JOIN user_role ur ON ur.user_id = u.id
JOIN role r ON r.id = ur.role_id
WHERE u.deleted_at IS NULL AND u.status = 'active';

SELECT COUNT(*) AS admin_and_pr_manager FROM (
  SELECT ur.user_id
  FROM user_role ur
  JOIN role r ON r.id = ur.role_id
  JOIN "user" u ON u.id = ur.user_id
  WHERE u.deleted_at IS NULL AND u.status = 'active'
    AND r.code IN ('admin', 'pr_manager')
  GROUP BY ur.user_id
  HAVING COUNT(DISTINCT r.code) = 2
) t;

-- S6 通配与自定义授权（确认新 scope 不会被已有授权命中，A1 / A4）
SELECT scope FROM permission
WHERE scope LIKE '%*%' OR scope LIKE 'negotiation%'
ORDER BY scope;

SELECT p.scope, upo.effect, COUNT(*) AS users
FROM user_permission_override upo
JOIN permission p ON p.id = upo.permission_id
WHERE p.scope = '*' OR p.scope LIKE 'negotiation%' OR p.scope LIKE 'product%'
GROUP BY p.scope, upo.effect
ORDER BY p.scope;

-- S7 PR / 主管账号有没有 product 读权限（A-02 / A-21 / Q11）
--     结果为 0 且 S6 里没有 product% 的 grant = 这些账号打开款式下拉会 403
SELECT COUNT(DISTINCT ur.user_id) AS pr_side_users_with_product_read
FROM user_role ur
JOIN role r ON r.id = ur.role_id
WHERE r.code IN ('pr', 'pr_manager')
  AND ur.user_id IN (
    SELECT ur2.user_id
    FROM user_role ur2
    JOIN role_permission rp ON rp.role_id = ur2.role_id
    JOIN permission p ON p.id = rp.permission_id
    WHERE p.scope IN ('*', 'product.*:*', 'product.*:read', 'product:read')
  );

-- S8 博主 / 款式数量（谈款博主下拉只加载前 100 个；催发批量款式下拉请求 200 条）
SELECT COUNT(*) AS active_bloggers FROM blogger WHERE is_deleted = false AND is_active = true;
SELECT COUNT(*) AS styles FROM style WHERE is_deleted = false;

-- S9 仓库打单存量（057 回填量；不输出地址原文）
SELECT
  COUNT(*) FILTER (WHERE COALESCE(BTRIM(source_extra->>'打单地址'), '') <> '') AS has_addr,
  COUNT(*) FILTER (WHERE COALESCE(BTRIM(source_extra->>'发货单号'), '') <> '') AS has_waybill,
  COUNT(*) FILTER (WHERE COALESCE(BTRIM(source_extra->>'打单地址'), '') <> ''
                     AND COALESCE(BTRIM(source_extra->>'发货单号'), '') = '')  AS waiting_print,
  COUNT(*) FILTER (WHERE source_extra->>'打单地址' ~ '1[3-9][0-9]{9}')          AS addr_contains_mobile,
  COUNT(*) FILTER (WHERE source_extra ? '寄回单号')                              AS stale_return_waybill_key
FROM promotion WHERE is_active = true;

SELECT k, COUNT(*) FROM promotion, jsonb_object_keys(source_extra) AS k
GROUP BY k ORDER BY 2 DESC;

-- S10 催发存量（059 与 Q9 的影响面）
SELECT status, close_reason, COUNT(*) FROM urge_task GROUP BY 1, 2 ORDER BY 1, 2;
SELECT trigger_type, COUNT(*) AS n, COUNT(screenshot_attachment_id) AS with_screenshot
FROM urge_record GROUP BY 1;
SELECT no_publish_days, max_urge_times, max_overdue_days,
       urge_threshold_days, important_threshold_days, auto_scan_enabled
FROM urge_config;

-- S11 谈款上线后不经谈款直接建的推广单（Q10）
SELECT p.created_at::date AS d, COUNT(*) AS created, COUNT(n.id) AS from_negotiation
FROM promotion p
LEFT JOIN negotiation n ON n.promotion_id = p.id
WHERE p.created_at >= DATE '2026-10-01'
GROUP BY 1 ORDER BY 1;
```

## 6. 建议实施顺序

1. **立即可做，不等确认**（S）：催发批量弹窗的款式下拉改服务端搜索（A-21）；谈款博主下拉改服务端搜索（A-02）；催发弹窗接上现成的截图上传接口（A-22 的前一半，截图仍可选）。同时跑 §5 的 S4 / S5 / S7。
2. **推广单收件 / 发货 typed 化 + 仓库页**（迁移 057；A-13 ~ A-16、A-23、A6 推广单侧字段权限；M~L）。不依赖谈款改造，先上还能顺手关掉两个现存问题：运营能看到打单地址、PR 的「录入信息」会冲掉仓库单号。
3. **谈款两级审核 + 补收货地址 + 定稿后建单 + 时间线 + 36h**（迁移 058；A-04 ~ A-12；合计 L，约 3~4 天）。依赖第 2 步的推广单收件列；依赖 Q1 / Q2 / Q3 / Q5。
4. **博主卡片驳回记录**（A-08；S~M）。依赖第 3 步的终态语义和时间线表。
5. **催发「标记完成」+ 按款式筛选 + 我的催发**（迁移 059；A-21 / A-22 余下部分；M）。独立，可与 2 / 3 并行；依赖 Q9。
6. **推广单来源收窄**（A-18；S）。依赖 Q10，可并进第 3 步一起上。

## 7. 顺带发现（不在本分支的图节点内，供汇总参考）

1. **寄回单号两个入口**：推广「录入信息」弹窗仍有「寄回单号」（PromotionListPage.tsx:87），写进 `source_extra`；寄拍审核门槛读的是 typed `return_waybill`（P/service.py:1083），047 已经把 JSONB 副本清掉了（MIG/047_mode_flow_fields.py:64）。PR 在弹窗里填了，审核照样过不去。属于图 4 节点 7「等待上传博主寄回衣服单号」，提醒负责分支；S9 的 `stale_return_waybill_key` 能看出有没有人这样填过。
2. **谈款没有行级归属**：任何持 `negotiation:write` 的人都能改 / 提交别人的谈款（N/service.py:182-280 不校验 `pr_id`），列表也不按人过滤；PRD 模块一「主管：查看全部单据」暗示 PR 只看自己的。本次图没要求，列出备查。收货信息上了谈款单之后，所有 PR 都能看到所有博主的收件电话——与现在所有 PR 都能看博主 phone 的口径一致。
3. **hover 卡「历史合作」取的是全部有效推广单**（含未发布，N/repository.py:164-167），PRD 改动 3 原文是「已完成 / 已结款单」。不在本次图里，没改。
4. **站内通知后端已有，前端没有消费方**（backend/app/modules/wecom/notification_api.py:14）。凡是「提醒」类需求，目前只能做成列表高亮和计数。
