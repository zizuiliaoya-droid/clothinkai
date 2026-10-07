# 调研报告 B：推广单 → 主管审核 → 财务付款 → 结款截图 → 7 天数据 → 已完结 + 「品名」改用商品简称

> 范围：图 4（`站外推广（催发任务）.png`）节点 6~9，加业务方 10-05 已定的「推广列表 / 仓库页『品名』与企微 `{商品简称}` 改用商品简称」。
> 基线：`main` @ `a0f1410`，alembic head `056_goods_short_name`。只读调研：没有改代码、没有连生产。
> 路径简写：`promotion/…`、`finance/…`、`urge/…` 等 = `backend/app/modules/…`；`core/…` = `backend/app/core/…`；`tasks/…` = `backend/app/tasks/…`；`alembic 0xx` = `backend/alembic/versions/0xx_*.py`；`tests/…` = `backend/tests/…`；`*Page.tsx` = `frontend/src/pages/…`；`features/…` = `frontend/src/features/…`。

## 0. 结论

图 4 节点 6~9 与 PRD V1.4 原流程图（docx「Mermaid 推广管理完整状态流转」节）几乎逐节点一致，**大部分缺口是 PRD 原本就有、实施时没做的**：推广单被主管驳回后没有任何重新提交入口（死单）；召回完成后单据进不了主管审核和结款；财务在系统里看不到博主收款码，也看不到任何推广单备注；「待 PR 通知博主 / 已结款 / 已完结」三个状态都不存在；「发布满 7 天」在代码里没有任何判断。新图真正改口径的只有两处，都和已定决定冲突：「已发布即可复盘」（台账 4b-1 定的是结完款、录完 7 天数据才进复盘）、「置换也进待财务付款」（PRD + 批次 2b 定的是置换直接已结款、不建结款单）。

已定的「品名 / `{商品简称}` 改用商品简称」**不需要 migration**：推广列表 SQL 已经 LEFT JOIN 了 `goods_main`，只是没取 `short_name`；改法是一条共享表达式「商品简称 → 回落建单快照」。生产 264 个简称全空，上线当天页面不会有变化。

一处要纠正的前提：台账说「全系统有一批查询按 `settlement_status = '已付款'` 过滤（索引、汇总、财务列表）」——那批查询在**结款单表 `settlement`** 上；**推广单** `promotion.settlement_status = '已付款'` 在后端只有 `record_metrics` 一处门槛读取（前端 3 处判断 + 1 处配色），报表模块 0 命中。所以「已结款」推荐直接加在推广单侧的结款状态机上，结款单表不动（§2-B9）。

**计数（主表 25 行）**：已满足 3 ／ 部分满足 9 ／ 缺失 9 ／ 冲突 2 ／ 现有缺陷·风险 2（顺手发现，不是图上节点）。不含已满足项，粗估合计约 20 人日。

## 1. 主表

工作量：S ≤ 0.5 天、M 1~2 天、L ≥ 3 天。

| 编号 | 图·节点 | 需求（摘） | 现状（证据 文件:行） | 判断 | 建议改动（后端 / 前端 / 迁移 / 权限） | 工作量 | 依赖 / 风险 |
|---|---|---|---|---|---|---|---|
| B-01 | 图4·整条状态链 | 图上是一条线性状态：召回中 → 提交主管审核 → 待 PR 重新处理 → 待财务付款 → PR 通知博主 → 已结款 → 已完结 | 状态拆在 4 个并行状态机：publish / recall / settlement / retro（promotion/state_machines.py:49, 142, 209, 282；promotion/enums.py:8-82），列表分 4 列显示（PromotionListPage.tsx:721-792）；没有一个字段能直接回答「这单卡在哪一步」 | 部分满足 | 后端：加派生字段 `stage`（不落库，Python + SQL CASE 双实现，照 `urge_status` 的做法 promotion/urge_calculator.py:60-105），列表可按它筛；前端：列表加「当前阶段」列。规则见 §2-B1 | M | 依赖 B-07~B-18 新增的字段；两份实现要一条一致性测试守住 |
| B-02 | 图4·博主情况 → 不合作 → 状态：已完结 | 不合作直接进终态 | `cancel()`：只允许未发布 → 已取消，`cancel_reason` 必填，同事务关催发任务（promotion/service.py:846-898；promotion/state_machines.py:63-69；promotion/schemas.py:177-182）；PRD 原图这个节点就叫「取消:终态」。报表按 `publish_status='已取消'` 计取消量，并从完成率 / 超时率分母扣除（report/advanced_repository.py:193-204, 771-772, 807；report/repository.py:71, 126） | 已满足（名称不同） | 不改存储值：继续叫「已取消」；图上的「已完结」只在 B-01 的派生阶段里显示成「已完结·不合作」 | S | 图上「已完结」同时用于不合作与全流程终点，不能共用一个存储值（Q1） |
| B-03 | 图4·已发布 → 录发布日期、笔记链接、截图 | 三项发布信息 | `publish()` 要求 `publish_url`（http/https）+ `actual_publish_date`（promotion/schemas.py:161-174），品牌词评论截图必传，且状态机先判（promotion/service.py:756-768）；前端发布弹窗三项都有（PromotionListPage.tsx:1584-1657） | 已满足 | 可选小修：`actual_publish_date` 加「不晚于今天」校验（`get_today()`），否则 B-17 的「发布满 7 天」可被未来日期绕过 | S | — |
| B-04 | 图4·已发布 → 上传收款码（召回完成同样要） | 收款码随发布一起提交 | 收款码只在「录入信息」弹窗单独上传，任何状态可传、任何环节不校验（promotion/service.py:489-586；promotion/api.py:165-230；PromotionListPage.tsx:1694-1761）；全库没有收款码必填门槛 | 部分满足 | 后端：`publish()` 在品牌词截图之后加「应付金额 > 0 时必须已有收款码」（置换常态不需要；校验顺序仍是状态机先判）；前端：发布弹窗内嵌收款码上传（复用 `uploadPaymentQrFile`）；测试：`promotion_factory` 加 `payment_qr=True`（照 `brand_comment=True`） | S | 会挡业务：没传收款码的单发不了布；所有走 publish 的测试要补工厂参数（台账 4b-2 统计过 11 个） |
| B-05 | 图4·已发布 →「已发布即可复盘，无需等 7 天」 | 发布后就能写复盘 | 复盘的唯一入口是 `record_metrics`（录 7 天数据）把 retro 未开始 → 待复盘（promotion/state_machines.py:224-230），而它要求 `settlement_status='已付款'`（promotion/service.py:1251-1258）；`submit_retro` 只能从「待复盘」进（promotion/state_machines.py:232-237）；前端「写复盘」只在待复盘可点（PromotionListPage.tsx:902-910） | 冲突（台账 4b-1：结完款、录完 7 天数据才复盘） | 推荐按新图：复盘与 7 天数据解耦。retro 状态机改为「未开始 --submit_retro（已发布）--> 待确认 --confirm--> 已完成 / --reject--> 待复盘（重写）」；`record_metrics` 不再推进 retro_status；hover 卡「只展示已确认复盘」保留（promotion/repository.py:558-589）；前端「写复盘」条件改为已发布 | M | tests/integration/test_retrospective.py 与 tests/unit/test_retro_state_machine.py 要改；存量 retro_status≠未开始 的单需核对（§4 SQL-6）；Q2 |
| B-06 | 图4·博主情况 → 样品需要寄回 → 召回中 | 超时未发的单直接进召回 | `start_recall` 要求 publish_status ∈ {已发布, 已取消}（promotion/service.py:911-922），前端同样禁用（PromotionListPage.tsx:859-870）。图上召回分支来自「催发 → 博主情况」，即超时未发布的单——现在要先「取消」再「召回」两步 | 部分满足 | 后端：允许从「未发布 / 异常」直接发起召回，同事务把 publish_status 置「已取消」（cancel_reason 写「召回：原因」）并关催发任务（复用 `UrgeCloseReason.CANCELLED`，不用改 alembic 049:149-152 的 CHECK）；前端：召回按钮放开未发布 | S | 报表「取消量」会包含召回导致的取消——与今天两步操作的结果一致；若要求取消量只算不合作，需改报表口径（另议） |
| B-07 | 图4·召回中 → PR 上传寄回单号截图 | 必须上传截图（红框） | 不存在：召回只有 `recall_reason` 文本（promotion/schemas.py:193-198）；附件用途白名单没有召回（core/attachment.py:156-165）；`return_waybill` 是寄拍的寄回单号文本（promotion/models.py:123-128），审核门槛只认它（promotion/service.py:1077-1089） | 缺失 | 迁移：promotion 加 `recall_waybill_attachment_id`（FK attachment）；后端：用途 `recall_waybill_screenshot` + `POST /api/promotions/{id}/recall/waybill`（multipart，后端代传 + 失败补偿删除，照 `upload_brand_comment` promotion/service.py:1674-1758）；召回成功要求已有该截图。不复用 `return_waybill`（理由 §2-B4） | S | 一列迁移；寄拍单被召回时与 B-12 门槛的衔接见 §2-B4 |
| B-08 | 图4·召回完成：录入运费、收款码，后台更新推广成本 | 召回完成一步录运费 + 收款码 | `recall_success` 不收任何字段：API 收了 `remark` 却不传给 service（promotion/api.py:312-339；promotion/service.py:973-1041）。`return_shipping_fee` 已有，且在生成列 `total_promo_cost` 里（promotion/models.py:120-142），PATCH 可改并进金额时间线（promotion/service.py:432-441）——但前端没有任何寄回运费输入：召回成功弹窗写「寄回运费可以在『录入信息』里补」（PromotionListPage.tsx:1561-1565），录入信息弹窗里实际没有这一项（1659-1807） | 部分满足 | 后端：`recall_success` 改收 `return_shipping_fee`（必填 ≥0）、要求召回截图（B-07）、应付 > 0 时要求收款码；写金额时间线（change_source 用「手动编辑」；若要新增「召回录入」需改 `ck_amount_log_change_source` promotion/models.py:472-475）；前端：召回成功表单加运费与收款码 | S | 「后台更新推广成本」更新了，但报表只统计已发布（report/advanced_repository.py:397, 544, 730, 878, 916）：未发布就召回的单，其运费永远不进报表（Q12） |
| B-09 | 图4·召回完成 → 提交主管审核 → … → 待财务付款 | 召回单也要过主管审核再结款 | 断链：approve 要求 publish_status='已发布'（promotion/service.py:1065-1073），待核查只能由 publish 推进（promotion/service.py:794-807）；召回的单 publish_status 是已取消、settlement_status 停在「未核查」，永远进不了审核与结款；召回成功无出边（promotion/state_machines.py:148-174） | 缺失 | 后端：召回成功同事务 settlement_status 未核查 → 待核查（复用 auto_advance）；review 的 approve 前置改为「已发布 或 召回成功」；召回单应付 = 寄回运费（不付服务费，Q4）；寄拍门槛接受「召回成功且有召回截图」 | M | 依赖 B-07、B-08、B-13；召回单已结款后是否直接完结见 Q5 |
| B-10 | 图4·主管审核 → 驳回 → 待 PR 重新处理，补充文字说明 → 回到提交主管审核 | 驳回后 PR 补说明再提交 | 死单：reject → settlement_status='已驳回'（promotion/service.py:1097-1105）。状态机定义了「已驳回 → 待核查 resubmit」（promotion/state_machines.py:324-330），promotion 模块没有任何方法或路由实现它；前端审核按钮只在待核查可点（PromotionListPage.tsx:871-887）。PR 只能改可覆盖的 `remark`（promotion/schemas.py:127）；驳回原因只留最后一次（promotion/models.py:241-244）；发布后 `publish_url` / `actual_publish_date` 没有任何修改入口（`PromotionUpdate` 不含它们 promotion/schemas.py:104-129） | 缺失 | 后端：`POST /api/promotions/{id}/resubmit`（`note` 必填 ≤2000，截图可选——PRD「截图非必填」；可顺带改发布链接 / 发布日期 / 重传评论截图），已驳回 → 待核查，重提说明与上一轮驳回原因写进时间线（B-19）；前端：操作菜单加「重新提交」弹窗；权限：挂 `promotion:write` | M | 依赖 B-19 存历史；不做 B-19 也能先上，但驳回原因与重提说明会被下一轮覆盖 |
| B-11 | 图4·备注同步财务；待财务付款「财务依据单据备注结款」 | 财务看得到单据备注 | 财务什么都看不到：结款单 `remark` 事件建单时不写（finance/listeners.py:78-90，只有 Excel 导入会填 importer/adapters/settlement.py:209），结款页「备注」列显示的就是这个空字段（SettlementListPage.tsx:241）；`SettlementResponse` 没有推广单备注、驳回原因、收款码（finance/schemas.py:129-181）；财务角色没有 `promotion:read`（auth/default_roles.py:192-211 + core/security/permissions.py:39-53），推广响应的收款码可见角色也排除财务（promotion/service.py:1864-1866）——**财务目前在系统里看不到博主收款码** | 缺失 | 后端：`SettlementResponse` 加只读推广单信息：推广编号、合作模式、发布链接、推广单 `remark`、驳回原因（分类 + 正文）与 PR 重提说明的历史、寄回运费、收款码签名 URL；列表批量取（照 `_enrich_display_fields` finance/service.py:643-675）避免 N+1；前端：结款页加「单据备注」列 + 详情抽屉 | M | 收款码是私有附件，只对结款单可见角色签 URL；历史记录依赖 B-19 |
| B-12 | 图4·审核通过 → 判断合作模式 → 寄拍：等待上传博主寄回衣服单号，无单号后端拦截 | 寄拍无单号不能流转财务 | 门槛放在审核前：寄拍没有 `return_waybill` 时 approve 直接 422（promotion/service.py:1077-1089），单号随时可补（promotion/service.py:1193-1220；promotion/api.py:367-382）。图上的顺序是「先审核通过 → 再等单号 → 单号到了自动流转」 | 已满足（顺序不同） | 硬约束已满足，推荐维持现状。可选（Q7）：寄拍无单号也允许通过，进新值「待寄回单号」，PR 补单号时自动推进并发 `SettlementRequested` | S（可选 M） | 若做可选项：推广单结款状态加值，前端枚举与配色同步（features/promotion/types.ts:16-21；PromotionListPage.tsx:113-119） |
| B-13 | 图4·送拍 / 置换 → 单号上传完成 → 待财务付款 | 置换也进财务 | 与 PRD / 2b 冲突：置换审核通过直接「已付款」，不发 `SettlementRequested`、不建结款单（promotion/service.py:1090-1093, 1161-1188；promotion/state_machines.py:305-315）；PRD 模块二硬规则 3 与流程图都写置换跳过财务（PRD docx「推广管理完整状态流转」节） | 冲突 | 推荐：按「应付金额」分流而不是按模式。应付 = 博主服务费（置换恒 0；未发布就召回的单不付）+ 寄回运费 + 附加项。应付 = 0 → 直接已结款（置换常态，PRD 规则不变）；应付 > 0 → 待财务付款（含置换被召回要报销运费）。`SettlementRequested.amount` 从 `quote_amount` 改为应付合计（promotion/service.py:1172） | S | 运费进应付后，财务侧「增加结算项·运费」（finance/service.py:510-570）会重复计，见 B-14；Q4 |
| B-14 | 图4·待财务付款，单向通道 | 主管通过即进财务，财务只付款不回退 | 「待财务付款」本身已经单向：结款单状态机从它出去只有 mark_paid（finance/state_machines.py:66-81）。但主管通过后结款单先落「待核查」（finance/listeners.py:77-90），还要「核查通过 → 待付款 → 填付款金额 → 待财务付款」两步人工（finance/service.py:116-242；SettlementListPage.tsx:58-61, 248-280），图上没有；核查权限 `settlement.review:approve` 主管和**财务**都有（auth/default_roles.py:182, 208；alembic 029:36-49）；结款单驳回后没有重提路由（service 有 `resubmit` finance/service.py:467-504，finance/api.py 无路由），推广单仍停在「待付款」——第二处死单 | 部分满足 | 后端：结款单由事件直接建在「待财务付款」，amount = 应付合计（B-13）；核查 / 驳回 / 填付款金额接口只留给 admin 兜底；财务上传凭证时填「实付金额」（默认 = 应付，写 `payment_amount`）；附加项允许主管在付款前追加（改 finance/service.py:526-531 与 finance/repository.py:212-218 的状态条件）；权限：从 finance 角色收回 `settlement.review:approve`；前端：结款页操作只剩「上传付款凭证」 | M | 生产在途的「待核查 / 待付款 / 已驳回」结款单要一次性处理（§4 SQL-3）；tests/integration/test_e2e_review_to_paid.py:65-110 整条重写；Q6 |
| B-15 | 图4·财务上传付款凭证，系统自动生成结款日期 | 日期系统生成 | 财务手填：Form `payment_date` 必填（finance/api.py:213-242），校验 ≤ 今天（finance/service.py:294, 360-367），写入结款单（finance/service.py:401-410）；前端日期框默认今天（SettlementListPage.tsx:189-195, 449-455）；旧 `PUT /payment-proof` 同样收（finance/api.py:194-210；finance/schemas.py:73-85） | 部分满足 | 后端：一律取 `get_today()`（Asia/Shanghai，promotion/urge_calculator.py:43-57），Form 字段改可选并忽略（照 `cooperation_date` 的兼容写法 promotion/schemas.py:72-78），旧 PUT 同样忽略；Excel 导入保留文件日期；前端：去掉日期框，改只读文字；测试 6 个文件（§2-B8） | S | 口径：结款日期从「实际付款日」变成「上传凭证当天」；报表不用这个字段（report 模块 0 命中），影响只在结款页列表 / 筛选与未被前端调用的日汇总接口；Q10 |
| B-16 | 图4·PR 通知博主 → PR 上传结款聊天截图 → 状态：已结款 | 付款后 PR 上传聊天截图才算已结款 | 不存在：付款后 `SettlementPaid` 把推广单 待付款 → 已付款就结束（promotion/listeners.py:32-60）；没有「待通知博主」状态，没有聊天截图用途（core/attachment.py:156-165）。已有的支撑：PR 在「录入信息」里能看到财务的付款凭证（PromotionListPage.tsx:1763-1775；promotion/repository.py:131-173） | 缺失 | 推荐**只在推广单侧**结款状态机加「已结款」：已付款 --notify_blogger（上传聊天截图）--> 已结款；应付 = 0 的审核通过直接到已结款。迁移：promotion 加 `settlement_chat_attachment_id`、`blogger_notified_at`；后端：用途 `settlement_chat_screenshot` + `POST /api/promotions/{id}/settlement-chat`（后端代传）；`record_metrics` 门槛改为已结款。结款单表不加值。影响清单见 §2-B9 | M | 存量「已付款」推广单要定是否补截图（§4 SQL-5）；tests/unit/test_retro_state_machine.py:72-73 断言「已付款是终态」要改写 |
| B-17 | 图4·发布满 7 天？→ PR 录入点赞收藏评论，上传截图 | 满 7 天才录，截图必传 | `record_metrics` 三指标 + 截图必传、同一请求同一事务（promotion/service.py:1259-1321；promotion/api.py:439-472），写 `metrics_recorded_at`（promotion/service.py:1319）；但门槛是「已付款」不是「已结款」（1251-1258），且**代码里没有任何「发布满 7 天」判断**，只有前端提示文字（PromotionListPage.tsx:783, 1341） | 部分满足 | 后端：门槛改为「已结款 且 `get_today() ≥ actual_publish_date + 7`」，未满 422 并告知可录日期；前端：按钮 disabled 条件同步，提示里显示可录日期 | S | 依赖 B-16；发布日期可填未来日期会让判断失真（B-03 小修） |
| B-18 | 图4·已完结终态 | 全流程收口的终态 | 没有全单终态。唯一的「完成」是复盘线的 retro_status='已完成'（主管确认复盘，promotion/state_machines.py:238-244），与结款、7 天数据都没有联系；录完 7 天数据后 PATCH 和采集器仍能改点赞（promotion/schemas.py:126；promotion/service.py:1548-1577） | 部分满足 | 迁移：promotion 加 `completed_at`；后端：「已结款 + 7 天数据已录（未发布的召回单免录）+ 复盘已确认」三者齐了，在最后一个动作的同事务里自动置 `completed_at`；之后 PATCH / 上传 / 采集器写点赞一律 409（admin 例外）；前端：派生阶段显示「已完结」 | M | 依赖 B-05、B-16、B-17；完结条件需业务确认（Q3、Q5） |
| B-19 | 图4·已完结：保存全流程时间线 + 截图，用于纠纷追溯 | 全流程可追溯 | 散在 5 处：audit_log（各动作都写，但只能 `GET /api/audit-logs` + `auth.audit:read` 读，且超过 12 个月归档删除 tasks/cleanup_tasks.py:49-104，core/config.py:127）、promotion_amount_log（promotion/models.py:415-487）、urge_record（urge/models.py:157-）、blogger_retrospective（promotion/models.py:490-551）、各附件外键。缺：单据级时间线接口与界面；召回 / 取消 / 重提没有时间字段；重传收款码与品牌词截图会覆盖外键、旧图失联（promotion/service.py:559-560, 1733-1734）；审核 audit 不记驳回原因正文（promotion/service.py:1144-1159） | 部分满足 | 推荐新建只追加事件表 `promotion_event`（单据、事件类型、from / to、说明、附件 id、操作人、时间；RLS；不进 audit 归档），一个 helper 在每个状态动作的同事务里写；只读接口 `GET /api/promotions/{id}/timeline` 合并事件表 + 金额时间线（字段级门控）+ 催发留痕 + 复盘；前端单据时间线抽屉。两种做法的代价对比见 §2-B10 | L | 不回填，上线前的单只显示列上已有的时间戳；约 15 个状态动作要接入 helper；读接口挂 `promotion:read`，运营也能看，金额与收款码要再按字段级 / 角色过滤 |
| B-20 | 已定（10-05）·推广列表「品名」、改归属弹窗标题、仓库页「品名」 | 改用商品简称 | 三处都读建单快照 `style_short_name_snapshot`（PromotionListPage.tsx:664, 1119；WarehousePage.tsx:85），快照 = 款式简称或款式全名（promotion/service.py:266, 277；importer/adapters/promotion.py:212）。列表 SQL 已 LEFT JOIN goods_main，但只取 goods_code / is_suit（promotion/repository.py:614-636）；单条路径另查 goods_code / is_suit（promotion/service.py:1903-1917） | 缺失 | 后端：一条共享表达式 `COALESCE(NULLIF(BTRIM(g.short_name), ''), p.style_short_name_snapshot)`，列表 CTE 选出 `display_short_name`（另带 `goods_title` 供悬停），单条路径用同规则的 Python helper；`PromotionResponse` 加 `display_short_name`、`goods_title`，`style_short_name_snapshot` 保留不动、不回填；前端：两页「品名」与弹窗标题改读 `display_short_name`，悬停看全称 | M | `goods_main_id` 为空的单回落快照（§4 SQL-10 核数）；简称 264 个全空，上线当天显示不变 |
| B-21 | 已定（10-05）·企微催发 `{商品简称}` | 同一规则 | `find_urge_candidates` 取快照（promotion/repository.py:186-227，第 208 行）→ `build_render_ctx`（wecom/scan_service.py:91-96）→「商品简称」变量（wecom/domain.py:15, 65-84；默认模板 wecom/template_service.py:15-20） | 缺失 | `find_urge_candidates` LEFT JOIN goods_main 选同一表达式；scan_service 改读它；变量名与白名单不动 | S | 生产 `wecom_config` 0 行（台账 4a），消息从未发出，风险低；聚合消息只取第一单的名字（wecom/scan_service.py:91-96）是既有限制 |
| B-22 | B11 关联·催发页「款式」、博主 hover「历史合作款式」 | 同一快照，口径应一致 | 催发列表 / 详情 `p.style_short_name_snapshot AS style_name`（urge/repository.py:24-45，第 31 行）→ UrgePage.tsx:218-232, 470-476；hover 历史 `p.style_short_name_snapshot AS style_name`（negotiation/repository.py:152-175，第 158 行）→ BloggerHoverCard.tsx:104-110 | 缺失（已定范围外） | 推荐同批改：两处 SQL 各 LEFT JOIN goods_main 输出 `display_short_name`，`style_name` 保留兼容，前端改读 | S | 不在 10-05 明确范围（Q8） |
| B-23 | B11 关联·推广列表关键词搜索 | 能用商品简称搜到 | keyword 只 ILIKE `internal_code` / `style_code_snapshot` / `style_short_name_snapshot`（promotion/repository.py:692-699；trgm 索引 alembic 006:205-222）；商品页已同时搜简称与全称（product/goods_repository.py:111-115） | 缺失 | 搜索条件加 `g.short_name ILIKE :kw`（可再加 `goods_title`，与商品页一致）；不加新索引；`idx_promotion_short_name_trgm` 保留 | S | goods_main 264 行、推广 5156 行，跨表 OR 本来也用不上 trgm，性能无感 |
| B-24 | 现有缺陷·「录入信息」里的寄回单号 / 点赞 / 收藏 / 评论 | — | `SOURCE_FIELDS` 仍有「寄回单号」「点赞数」「收藏数」「评论数」，写进 `source_extra`（PromotionListPage.tsx:82-95, 1677-1692），列表还按它们各出一列（793-801）。047 已把寄回单号提成 typed `return_waybill` 并删了 JSONB 副本（alembic 047:34-68），审核门槛只认 typed（promotion/service.py:1081）；10-02 已定 7 天点赞以 typed `like_count` 为准 → PR 在「录入信息」填的寄回单号过不了审核，列表出现两列「寄回单号」 | 现有缺陷 | 前端：`SOURCE_FIELDS` 删掉这 4 项（老数据 JSONB 原样留档、不迁移）；寄回单号只留「填寄回单号」入口 | S | 删除后 JSONB 里的旧值不再显示（寄回单号 JSONB 生产全空 alembic 047:14；点赞类需核对 §4 SQL-11） |
| B-25 | 现有风险·「主管审核」的权限 | 图上是主管审核 | PR 持 `promotion.*:*`（auth/default_roles.py:33, 146），`has()` 的前缀通配只看第一段（core/security/permissions.py:50-51），所以 PR 能调 `/promotions/{id}/review` 审别人的单，service 只挡自审（promotion/service.py:1058-1063）。台账批次 3 已记录这个漏洞 | 现有风险 | 权限：新建第一段不是 `promotion` 的 scope（如 `promotion_review:approve`），迁移授予 pr_manager，review 改挂它；保留自审校验 | S | 改变谁能审：先核对生产有没有 PR 审过别人的单（§4 SQL-9）；Q9 |

## 2. 逐题回答（证据）

### B1 推广单状态维度全景

| 字段 | 取值（定义位置） | 状态机 | 转移：动作 from → to（触发方法） |
|---|---|---|---|
| `publish_status` | 未发布 / 已发布 / 已取消 / 异常 / 已删除（promotion/enums.py:8-15） | PublishStatusMachine（promotion/state_machines.py:49-103） | publish 未发布 → 已发布（promotion/service.py:742-844）；cancel 未发布 → 已取消（846-898）；mark_abnormal、restore、delete（promotion/state_machines.py:71-103）**只有状态机定义，service 与路由都没实现**（全库 grep `mark_abnormal` 只命中状态机与 promotion/schemas.py:185-190） |
| `recall_status` | 未召回 / 召回中 / 召回成功 / 召回失败（promotion/enums.py:18-24） | RecallStatusMachine（promotion/state_machines.py:142-174） | start_recall 未召回 / 召回失败 → 召回中（promotion/service.py:900-971，前置 publish_status ∈ {已发布, 已取消} 911-922）；recall_success / recall_failure（973-1041）；召回成功无出边 |
| `settlement_status`（推广单侧） | 未核查 / 待核查 / 待付款 / 已付款 / 已驳回（promotion/enums.py:27-34） | SettlementStatusMachine（promotion/state_machines.py:282-338） | auto_advance 未核查 → 待核查（publish 同事务 promotion/service.py:794-807）；approve 待核查 → 待付款（review，发 `SettlementRequested` 1161-1188）；approve_barter 待核查 → 已付款（置换 1090-1093）；reject 待核查 → 已驳回（1097-1105）；resubmit 已驳回 → 待核查（**只有定义 promotion/state_machines.py:324-330，无实现**）；mark_paid 待付款 → 已付款（`SettlementPaid` 监听 promotion/listeners.py:32-60） |
| `retro_status` | 未开始 / 待复盘 / 待确认 / 已完成（promotion/enums.py:62-82；CHECK promotion/models.py:332-335） | RetroStatusMachine（promotion/state_machines.py:209-279） | record_metrics 未开始 → 待复盘（promotion/service.py:1226-1361）；submit_retro 待复盘 → 待确认（1363-1422）；confirm_retro 待确认 → 已完成、reject_retro 待确认 → 待复盘（1424-1510） |
| `settlement.settlement_status`（结款单表） | 待核查 / 待付款 / 待财务付款 / 已付款 / 已驳回（finance/enums.py:8-22） | finance/state_machines.py:39-89 | 事件建单即待核查（finance/listeners.py:77-90）；review approve / reject（finance/service.py:116-188）；fill_payment 待付款 → 待财务付款（194-242）；mark_paid 待财务付款 → 已付款（262-358、369-461）；reject 待付款 → 已驳回；resubmit（467-504，**finance/api.py 无路由**） |
| `urge_task.status` | 进行中 / 已关闭；close_reason 博主已发布 / 已取消 / 手动关闭（urge/enums.py:8-29；CHECK alembic 049:146-157） | — | publish / cancel 同事务关闭（promotion/service.py:840-841, 894-895） |
| 派生 `urge_status` | 已取消 / 已发布 / 已删除 / 未排期 / 档期内 / 催发 / 重要催发 / 超时（promotion/urge_calculator.py:60-105） | — | 按排期与今天实时算，不落库 |

决定能否推进的门槛字段（不是状态）：`cooperation_mode`、`return_waybill`、`brand_comment_attachment_id`、`payment_qr_attachment_id`、`metrics_recorded_at`、`retro_confirmed_*`、`reviewed_*` / `review_reason(_category)`（promotion/models.py:97-244）。`promotion.settlement_status` 与 `settlement.settlement_status` 都是 String(16) 且没有 CHECK（迁移目录里 grep 不到），加值不需要 DDL。

图 4 状态链 → 现有字段：

| 图 4 节点 | 现有对应 | 缺 |
|---|---|---|
| 档期内 PR 跟进 / 是否超过约定发布时间 | urge_status 档期内 / 超时 | — |
| 不合作 → 状态：已完结 | publish_status=已取消 | 只是名字（B-02） |
| 已发布 → 录发布日期、链接、截图、收款码 | publish_status=已发布，同事务 settlement 未核查 → 待核查 | 收款码门槛（B-04） |
| 已发布即可复盘 | retro 只能从「录 7 天数据」进入 | 冲突（B-05） |
| 样品需要寄回 → 召回中 | recall_status=召回中（要先已发布或已取消） | 未发布直接召回（B-06） |
| PR 上传寄回单号截图 | — | 字段 + 接口（B-07） |
| 召回完成：录运费、收款码，更新推广成本 | recall_status=召回成功（不收输入）；成本由生成列自动算 | 输入（B-08） |
| 召回 → 提交主管审核 | — | 召回单进不了待核查（B-09） |
| 提交主管审核 | settlement_status=待核查 | — |
| 驳回 → 待 PR 重新处理 → 再提交 | settlement_status=已驳回（无出路） | 重提（B-10） |
| 备注同步财务 | — | 财务可见信息（B-11） |
| 寄拍：等待上传寄回衣服单号 | 无单号时 approve 422 | 顺序不同（B-12） |
| 送拍 / 置换 → 待财务付款 | 送拍：推广单待付款 + 结款单待核查；置换：直接已付款 | 置换冲突（B-13）；结款单多两步（B-14） |
| 财务上传付款凭证，系统生成结款日期 | 结款单已付款，`payment_date` 财务填 | 自动日期（B-15） |
| PR 通知博主 → 上传结款聊天截图 → 已结款 | 推广单已付款即止 | 状态 + 字段 + 接口（B-16） |
| 发布满 7 天？→ 录点赞收藏评论 + 截图 | `record_metrics`（门槛已付款，无 7 天判断） | 门槛（B-17） |
| 已完结终态：全流程时间线 + 截图 | 无全单终态；时间线散在 5 处 | B-18、B-19 |

B-01 派生阶段 `stage` 的建议规则（自上而下取第一个命中）：

1. publish_status=已删除 → 已删除
2. `completed_at` 非空 → 已完结
3. recall_status=召回中 → 召回中
4. publish_status=已取消 且 recall_status ∈ {未召回, 召回失败} → 已完结·不合作
5. settlement_status=已驳回 → 待 PR 重新处理
6. settlement_status=待核查 → 待主管审核
7. （可选，B-12）settlement_status=待寄回单号 → 待寄回单号
8. settlement_status=待付款 → 待财务付款
9. settlement_status=已付款 → 待 PR 通知博主
10. settlement_status=已结款 → 已结款（附子标记：未满 7 天 / 待录 7 天数据 / 待复盘确认）
11. publish_status ∈ {未发布, 异常} → 取 urge_status（档期内 / 催发 / 重要催发 / 超时 / 未排期）

### B2 不合作 → 已完结

- 现在的「取消」就是图上的「不合作」：只允许未发布 → 已取消（promotion/state_machines.py:63-69；promotion/service.py:860-864）；`cancel_reason` schema 必填（promotion/schemas.py:177-182），service 再判一次（853-854）；同事务关闭催发任务（894-895）；前端取消弹窗必填原因（PromotionListPage.tsx:1164-1190）。PRD 原流程图这个节点叫「H[取消:终态]」。
- 「已完结」是改名，不是新状态。图上同一个词还用在全流程终点（节点 9「已完结终态」），两种终态不能共用一个存储值。
- 报表口径：取消量 = `publish_status='已取消'`（report/advanced_repository.py:193, 771-772, 807；report/repository.py:71, 126；导出列「取消量」report/export_service.py:38）；完成率 / 超时率分母扣除取消与召回（report/advanced_repository.py:198-204；report/work_progress_service.py:92-96）；工作进度汇总表存这些计数（台账 5b-1 / 054）。改存储值要动这 6 处 SQL，并按 054 规则清空 `report_summary_coverage`。
- 建议：不改值；在 B-01 派生阶段里显示「已完结·不合作」（Q1）。

### B3 已发布

`publish()`（promotion/service.py:742-844）现在要求：

1. 状态机先判：只能未发布 → 已发布（756-760）——已发布再点报「状态不对」而不是「缺截图」（台账 4b-2 的顺序规则）。
2. 品牌词评论截图必传（762-768）。截图独立上传 `POST /api/promotions/{id}/brand-comment`（promotion/api.py:385-411；promotion/service.py:1674-1758；用途 `brand_comment_screenshot` core/attachment.py:163），不限状态。
3. 请求体必填 `publish_url`（必须 http/https 开头）与 `actual_publish_date`（promotion/schemas.py:161-174）。`actual_publish_date` 没有「不晚于今天」校验。
4. 同事务推进结款状态未核查 → 待核查（794-807），发 `PromotionPublished`，关催发任务（821-841）。

收款码 `promotion_payment_qr`：

- 在「录入信息」弹窗里上传（PromotionListPage.tsx:1694-1761），走 `POST /api/promotions/{id}/payment-qr/upload`（promotion/api.py:180-202；promotion/service.py:489-586，后端代传 + 失败补偿），另有旧的 upload-init + PUT 绑定（promotion/api.py:165-216）与 DELETE（219-230）；scope `promotion.payment_qr:write`，PR 的 `promotion.*:*` 覆盖。
- **不限状态，哪一步都不必填**：全库没有收款码门槛（grep `payment_qr_attachment_id is None` 与「收款码…必」均 0 命中）。
- 推广响应只给 admin / platform_admin / pr / pr_manager 签收款码 URL（promotion/service.py:1864-1866），财务看不到（见 B5）。
- 结论：图上要求的「发布日期、笔记链接、截图」已满足（截图 = 评论截图），缺的是「收款码随发布必填」（B-04）。

「已发布即可复盘，无需等 7 天」：

- 现在复盘的准确进入条件：`record_metrics` 要求 `promotion.settlement_status == '已付款'`，否则抛 `SettlementNotPaidError`（promotion/service.py:1251-1258）；还要数据截图（1259-1271）和 retro_status=未开始（1273-1277），之后才到「待复盘」；`submit_retrospective` 只能从「待复盘」进（promotion/state_machines.py:232-237；promotion/service.py:1379-1383）。即**已付款 且 7 天数据已录，才能写复盘**。「发布满 7 天」本身没有代码判断（只有前端提示 PromotionListPage.tsx:783, 1341）。
- 改成发布即可复盘要动：
  1. RetroStatusMachine：`submit_retro` 新增 未开始 → 待确认（保留 待复盘 → 待确认 给被打回的重写），`record_metrics` 移出复盘状态机（promotion/state_machines.py:222-252）。
  2. `submit_retrospective` 加前置 publish_status=已发布；`record_metrics` 不再写 retro_status（promotion/service.py:1307-1321）。
  3. 前端：「写复盘」disabled 从「retro_status≠待复盘」改成「未发布，或已在待确认 / 已完成」（PromotionListPage.tsx:902-910）；复盘列里「已结款，发布满 7 天后可录数据」的提示挪到 7 天数据那一列（775-792）。
  4. 完结判定（B-18）不再等于 retro 已完成。
  5. 测试：tests/integration/test_retrospective.py（如第 121 行 `test_unpaid_promotion_rejected` 语义要换）、tests/unit/test_retro_state_machine.py。
- hover 卡「只展示已确认复盘」**保留**（promotion/repository.py:558-589 `confirmed_at IS NOT NULL`；promotion/api.py:509-524）：提前写复盘不改变「没过主管的是草稿、不进博主档案」这件事。单赞成本仍只在录完 7 天数据后计算（promotion/service.py:1962-1966），与复盘解耦无关。
- 另一种读法：如果图上的「复盘」指「提交主管审核」，那现在已经满足——approve 只要求已发布 + 待核查（promotion/service.py:1065-1073），发布即自动进待核查。列为 Q2。

### B4 召回

现有召回流程：

- 端点：`POST /api/promotions/{id}/recall/start`、`/recall/success`、`/recall/failure`（promotion/api.py:297-339）。
- 字段：`recall_status`、`recall_reason`（promotion/models.py:151, 227-229）；没有召回时间、召回单号、召回截图字段（时间只能从 audit_log 与 `updated_at` 推）。
- 发起：前置 publish_status ∈ {已发布, 已取消}（promotion/service.py:911-922），原因可选（promotion/schemas.py:193-198）；前端同样禁用（PromotionListPage.tsx:859-870）。
- 成功 / 失败：不收任何字段——API 收了 `PromotionRecallResultRequest.remark`，却没传给 service（promotion/api.py:312-339；promotion/schemas.py:201-206；promotion/service.py:973-1041）。召回成功无出边（promotion/state_machines.py:148-174）。
- 前端召回弹窗（PromotionListPage.tsx:1491-1568）召回成功后提示「寄回运费可以在『录入信息』里补」（1561-1565），但「录入信息」弹窗里没有寄回运费输入（1659-1807；前端 grep `return_shipping_fee` 只命中类型定义与金额时间线标签）。

召回成功后单据走向：

- 已发布后才召回：召回与结款互不影响，审核 / 付款照走（review 不看 recall_status，promotion/service.py:1043-1191）。
- 未发布（先取消）再召回：publish_status=已取消、settlement_status 停在「未核查」——approve 要求已发布（1065-1073），待核查只能由 publish 推进（794-807）→ **进不了主管审核，也进不了结款**，图上「召回完成 → 提交主管审核 → 财务付款」整段是断的。

运费：

- `return_shipping_fee` 已有（promotion/models.py:120-121，注释就写「召回流程里由 PR 录入」），在生成列 `total_promo_cost = quote_amount + COALESCE(cost_snapshot,0) + COALESCE(return_shipping_fee,0)` 里（promotion/models.py:130-142），PATCH 可改并自动进金额时间线（promotion/service.py:432-441）——「后台更新推广成本」已经由数据库保证。
- 但它**不进应付**：`SettlementRequested.amount = quote_amount`（promotion/service.py:1172）；财务侧另有「增加结算项·运费」（finance/enums.py:25-30；finance/service.py:510-570）——同一笔运费两处录入、互不同步。
- 报表成本只统计已发布（report/advanced_repository.py:397, 544, 730, 878, 916）：未发布就召回的单，寄回运费永远不进报表（Q12）。

寄回单号截图——推荐独立字段，不复用 `return_waybill`：

1. `return_waybill` 是**文本**，语义是「寄拍博主拍完寄回的单号」，是寄拍审核的硬门槛（promotion/service.py:1077-1089），列表对寄拍缺单号标「待填」（PromotionListPage.tsx:759-774）；召回要的是**截图**。
2. 送拍 / 置换的召回如果写进 `return_waybill`，这个字段对送拍置换也有了值，语义变成「寄拍单号或召回单号」，以后按它统计或做门槛都会混。
3. 衔接：寄拍单被召回时，衣服就是这一次寄回的。寄拍门槛应接受「召回成功且有召回截图」，而不是让 PR 再填一遍 `return_waybill`。
4. 落地：promotion 加 `recall_waybill_attachment_id`（FK attachment）；用途 `recall_waybill_screenshot` 加进白名单（core/attachment.py:156-165；`attachment.purpose` 是 String(32) 且无 CHECK，core/attachment.py:122-123, 146-151，加用途不需要 DDL）；`POST /api/promotions/{id}/recall/waybill` 照 `upload_brand_comment` 的后端代传 + 补偿删除骨架（promotion/service.py:1674-1758）。

### B5 主管驳回 → 待 PR 重新处理

现状：

- 驳回：`review(action=reject)` 必填 `review_reason` + 三选一分类（promotion/schemas.py:209-226；promotion/service.py:1097-1105），写在推广单上（promotion/models.py:241-244）；前端驳回弹窗（PromotionListPage.tsx:1197-1240），列表结算状态旁显示分类、悬停看原因（733-747）。
- 驳回后状态：推广单 settlement_status=已驳回。
- 重新提交：**没有**。状态机定义了 已驳回 → 待核查 resubmit（promotion/state_machines.py:324-330），但 promotion 模块没有任何方法或路由实现它（grep `resubmit` 在 promotion 只命中状态机）；前端审核按钮只在待核查可点（PromotionListPage.tsx:871-887）→ 被驳回的推广单永远停在已驳回。
- PR 附说明：没有专门字段，只能改 `promotion.remark`（promotion/schemas.py:127），可覆盖、无历史。驳回原因也只留最后一次（下一次审核覆盖），审核的 audit 不记原因正文（promotion/service.py:1144-1159）。发布后的发布链接 / 发布日期没有修改入口（`PromotionUpdate` 不含这两个字段，promotion/schemas.py:104-129）——主管因为「链接不对」驳回，PR 也改不了。

财务结款页现在能看到什么：

- `SettlementResponse`（finance/schemas.py:129-181）：结算单号、金额（字段级权限）、付款日期与凭证、结款单自己的 `note_title` / `remark`、结款单自己的审核字段、`paid_by`、货号 / 款式名 / 博主昵称（finance/service.py:643-675 富化）、附加项。
- 结款页列（SettlementListPage.tsx:197-290）：月份、日期、大类、项目、货号、款式、博主名、结算单号、金额、付款金额、付款日期、总成本、付款图片、结算状态、备注、操作。
- 「备注」列是结款单自己的 `remark`，事件建单时根本不写（finance/listeners.py:78-90），只有 Excel 导入会填（importer/adapters/settlement.py:209）→ 系统生成的结款单这一列恒为空。
- 财务角色没有 `promotion:read`（auth/default_roles.py:192-211；它持有的 `promotion.urge:read` 通配不到 `promotion:read`，core/security/permissions.py:39-53），推广单页面和接口对财务都是 403；推广响应的收款码可见角色也排除财务（promotion/service.py:1864-1866）。**结论：财务在系统里看不到推广单备注、驳回 / 重提记录，也看不到博主收款码。**（这是按默认角色矩阵读代码得出的，生产授权可能被手工调整过，见 §4 SQL-8。）

图上「备注同步财务」「财务依据单据备注结款」，推荐财务看到：

1. PR 的重新处理说明（全部历史，倒序）；
2. 主管驳回原因（分类 + 正文，全部历史）；
3. 推广单 `remark`；
4. 博主收款码（签名 URL）；
5. 推广编号、合作模式、发布链接、寄回运费（让财务看得懂应付怎么来的）。

做法：`SettlementResponse` 加一组只读 `promotion_*` 字段，列表时批量取（照 `_enrich_display_fields`）；历史记录取 B-19 的事件表；结款页加「单据备注」列与详情抽屉。

### B6 合作模式分支

现状：

- 寄拍：approve 前必须有 `return_waybill`，否则 422（promotion/service.py:1077-1089）→ 待付款 + `SettlementRequested`。
- 送拍：→ 待付款 + `SettlementRequested`（promotion/service.py:1094-1096, 1161-1188）。
- 置换：→ 直接「已付款」（approve_barter，promotion/state_machines.py:305-315；promotion/service.py:1090-1093），不发 `SettlementRequested`、不建结款单（1161-1163）；前端提示「置换无需付款，已直接结清」（PromotionListPage.tsx:487-492）；测试 tests/integration/test_promotion_crud.py:1347-1372 守着。
- 依据：PRD 模块二硬规则 3 与流程图「置换 → 已结款，跳过待财务付款、财务付款、通知博主」（PRD docx「推广管理完整状态流转」节；promotion/enums.py:104-117 注释）。

冲突：图 4 画成「送拍 / 置换 → 单号上传完成 → 待财务付款」。

| 选择 | 影响 |
|---|---|
| A 维持 PRD（置换不进财务） | 零改动。但置换单被召回、博主垫付了运费时没有付款路径（图上召回完成要上传收款码） |
| B 照图（置换也进财务） | 每张置换单都要建一张 0 元结款单，财务逐张上传凭证、PR 还要传 0 元的结款聊天截图——正是 2b 刻意避免的；要恢复置换的 `SettlementRequested`，test_promotion_crud 1347-1372 改写 |
| **C（推荐）按「应付金额」分流** | 应付 = 博主服务费（置换恒 0；未发布就召回的单不付）+ 寄回运费 + 附加项。应付 = 0 → 审核通过直接已结款（置换常态仍是 PRD 规则）；应付 > 0 → 待财务付款（含置换召回报销运费）。改动：`review()` 分支条件从 `is_barter` 改为「应付 = 0」（promotion/service.py:1075-1096），`SettlementRequested.amount` 改为应付合计（1172），approve_barter 的含义改为「无需付款」。工作量 S |

注意：C 让寄回运费进入应付后，财务侧「增加结算项·运费」要停用或改名，否则重复计（B-14）。

### B7「待财务付款，单向通道」

现状是两级审核：

1. 推广单审核（主管）：`POST /api/promotions/{id}/review`，scope `promotion.review:approve`（promotion/api.py:342-364），通过即发 `SettlementRequested`。
2. 结款单建在「待核查」（finance/listeners.py:77-90），结款页再「核查通过 / 驳回」：`PUT /api/settlements/{id}/review`，scope `settlement.review:approve`（finance/api.py:148-160；finance/service.py:116-188）——**主管和财务都持有**（auth/default_roles.py:182, 208；alembic 029:36-49）。状态机里写的 actor_roles（pr_manager / admin，finance/state_machines.py:43-57）不会被校验，`assert_can_transition` 只比较 from / to / action（finance/state_machines.py:91-112）。
3. 「填付款金额」待付款 → 待财务付款（finance/service.py:194-242），写权限 `settlement.payment_amount` 主管和财务都有（core/security/field_permissions.py:67-74）。
4. 财务上传凭证 待财务付款 → 已付款，只有持 `finance.settlement:pay` 的财务 / admin 能做（finance/service.py:761-769）。

「单向」的现状：结款单一旦进了「待财务付款」，状态机只有 mark_paid 一条出边（finance/state_machines.py:66-81）——**待财务付款本身已经是单向的**。问题在它前面多出两步人工（结款单核查 + 填付款金额），图上没有；而且结款单驳回是第二个死单：service 有 `resubmit`（finance/service.py:467-504）但没有路由（finance/api.py 全部端点 57-266），推广单那边的 settlement_status 仍停在「待付款」、不会同步。

两级审核的关系：第二级审的是同一份 PR 提交，没有新信息；它实际承载的是「附加项」和「付款金额」两个输入。

图上「主管审核通过 → 待财务付款（单向）」意味着去掉财务侧的核查 / 驳回。推荐：

- 后端：`SettlementRequested` 处理器直接把结款单建在「待财务付款」，amount = 应付合计（B-13）；「核查 / 驳回 / 填付款金额」接口只留给 admin 兜底，结款页不再展示；财务上传凭证时填「实付金额」（默认 = 应付，写 `payment_amount`），替代主管的填付款金额；附加项改为允许主管在付款前追加（finance/service.py:526-531 与 finance/repository.py:212-218 的状态条件）。
- 权限：从 finance 角色收回 `settlement.review:approve`（default_roles + 迁移 revoke；权限有缓存，见 core/security/permissions.py 的 `_save_to_cache`，生效要等缓存过期或清缓存）。
- 前端：结款页操作只剩「上传付款凭证」（SettlementListPage.tsx:248-280）。
- 影响：在途的「待核查 / 待付款 / 已驳回」结款单要一次性处理（§4 SQL-3）；tests/integration/test_e2e_review_to_paid.py:65-110 整条旅程重写；财务日汇总分桶（finance/service.py:681-721）照常可用，只是待核查 / 待付款以后恒为 0。
- 如果业务要保留「财务退回」（例如收款码不对）：推荐财务不改状态，只在单据上留言（写事件表）；PR 换收款码本来就不需要状态变更（`upload_payment_qr` 不限状态，promotion/service.py:489-586）。

### B8「系统自动生成结款日期」

现状：`POST /api/settlements/{id}/payment-proof/upload` 的 Form 字段 `payment_date` 必填（finance/api.py:213-242，第 222 行），service 校验 ≤ 今天（finance/service.py:294, 360-367），`_mark_paid` 写入结款单（401-410），也进 audit 与 `SettlementPaid`（423-459）。旧 `PUT /payment-proof` 的 JSON 同样带 `payment_date`（finance/api.py:194-210；finance/schemas.py:73-85）。前端日期框默认今天（SettlementListPage.tsx:189-195, 449-455），调用 `uploadPaymentProof(settlementId, paymentDate, file)`（features/finance/api.ts:78-93）。

改成服务端自动取 `get_today()`（Asia/Shanghai，promotion/urge_calculator.py:43-57）要动：

- 后端：Form `payment_date` 改为可选并**忽略**（照 `cooperation_date`「HTTP 忽略、兼容旧客户端」的写法 promotion/schemas.py:72-78），`upload_payment_proof_file` 内部取 `get_today()`；旧 PUT 的 `SettlementPaymentProofRequest.payment_date` 改可选、同样忽略；`_check_payment_date` 可删。Excel 导入保留文件里的付款日期（importer/adapters/settlement.py:56, 146-150, 206），否则历史数据导不进来。
- 前端：删除日期框，换成只读文字「结款日期：系统自动记为上传当天」；`uploadPaymentProof` 不再传日期。
- 测试（grep `payment_date` 命中）：tests/api/test_settlement_api.py、tests/integration/test_settlement_proof_upload.py、test_settlement_mark_paid.py、test_e2e_review_to_paid.py、tests/unit/test_settlement_paid_event.py、tests/conftest.py；导入相关的 test_import_settlement.py、test_settlement_adapter.py 不变。

`payment_date` 的全部用途与口径影响：

| 用途 | 位置 | 改自动后 |
|---|---|---|
| 结款页「月份 / 日期」列（无付款日时回落建单日）、「付款日期」列 | SettlementListPage.tsx:198-207, 216-221 | 显示上传当天 |
| 列表按付款日期筛选 | finance/api.py:77-78, 92-94；finance/repository.py:294-297 | 筛的是上传日 |
| 日汇总「当日付款」 | finance/repository.py:392-399（前端没有调用，features/finance/api.ts:97-115 只有定义） | 同上 |
| `SettlementPaid.payment_date` | finance/events.py:46；推广侧监听不读它（promotion/listeners.py:32-60） | 无影响 |
| audit 字段清单 | finance/domain.py:39, 146 | 无影响 |
| 报表 / BI / 汇总表 | report 模块 0 命中 | 无影响 |

口径：结款日期从「实际付款日」变成「上传凭证当天」——先付款、隔天或跨月才补传凭证的单，会落到补传那天 / 那个月。图上明确要求系统生成，列为 Q10 让业务知情。

### B9「PR 通知博主 → PR 上传结款聊天截图 → 状态：已结款」

现状：财务 mark_paid → `SettlementPaid` → 推广单 待付款 → 已付款（promotion/listeners.py:32-60）就结束；没有「待 PR 通知博主」，没有结款聊天截图用途（core/attachment.py:156-165）。

`'已付款'` 的全量 grep（backend/app 与 frontend/src，排除注释与 order_adjustment 的同名状态）：

推广单 `promotion.settlement_status`：

| 位置 | 用途 | 加「已结款」后 |
|---|---|---|
| promotion/service.py:1251-1258 | `record_metrics` 门槛（**后端唯一的读取**） | 改为已结款 |
| promotion/service.py:1090-1093；promotion/state_machines.py:305-315 | 置换审核通过写已付款 | 应付 = 0 时写已结款 |
| promotion/listeners.py:39-45；promotion/state_machines.py:331-337 | 财务付款回写已付款 | 不变 |
| promotion/repository.py:657-659；promotion/api.py:94；promotion/schemas.py:481 | 列表按用户所选状态筛 | 枚举自动多一个值 |
| promotion/models.py:309-313 | 普通复合索引 (tenant_id, settlement_status) | 不受影响 |
| PromotionListPage.tsx:113-119, 487-492, 780-791, 891-901；features/promotion/types.ts:16-21 | 配色、审核提示、复盘列提示、录 7 天按钮 | 同步改 |
| tests/unit/test_retro_state_machine.py:72-73 | 断言「已付款」无出边 | 改为断言「已结款」是终态；正交断言（75-80）不受影响 |
| tests/integration/test_promotion_crud.py:1347-1372；tests/integration/test_retrospective.py:121 | 置换 → 已付款；未付款不能录数据 | 改断言 |
| report 模块（汇总、BI、导出） | grep `settlement_status` / `已付款` / `settlement` 均 0 命中 | 无 |

结款单 `settlement.settlement_status`（finance 表，**不加值**）：日汇总分桶（finance/service.py:681-721；finance/repository.py:327-365）、列表筛选（finance/repository.py:274-275）、状态机（finance/state_machines.py:74-81）、导入校验（importer/adapters/settlement.py:46-48, 151-155）、结款页（SettlementListPage.tsx:42-55, 163）——都不受影响。台账说的「一批按已付款过滤的查询」就是这一组，它们在结款单表上。

推荐方案：

- 只在**推广单侧**结款状态机加「已结款」：已付款 --notify_blogger（PR 上传结款聊天截图）--> 已结款；应付 = 0 的审核通过直接到已结款。这条链本身就是结款语义（图上是线性的 付款 → 通知 → 已结款），与 4b-1 把复盘单开字段的理由（复盘与结款正交）不同，不违背那次决定。
- 迁移：promotion 加 `settlement_chat_attachment_id`（FK attachment）、`blogger_notified_at`；`settlement_status` 加值不需要 DDL；数据：存量置换「已付款」改为「已结款」（置换没有结款单，不可能补聊天截图）。
- 后端：用途 `settlement_chat_screenshot`；`POST /api/promotions/{id}/settlement-chat`（multipart，后端代传 + 失败补偿删除，照 promotion/service.py:1674-1758），前置 settlement_status=已付款，scope 挂 `promotion:write`（PR 覆盖；运营 `promotion.*:read`、仓库 `promotion:read` 都写不了）。
- 备选（不推荐）：不加值，用 `settlement_chat_attachment_id IS NOT NULL` 派生「已结款」——Python 与 SQL 各写一份判断，列表筛选也得写 CASE。

### B10 发布满 7 天 → 录数据 → 已完结 + 全流程时间线

7 天数据录入入口（promotion/service.py:1226-1361；promotion/api.py:439-472，scope `promotion.retro:write`）：

- 要求 settlement_status=已付款（1251-1258）——不是「已结款」（还不存在）；
- 三指标 + 截图必传，同一个 multipart 请求、同一事务（1259-1271, 1279-1359）；
- 写 `metrics_recorded_at`（1319），单赞成本以它为准（promotion/service.py:1962-1966；台账 5b-1e）；
- **不看发布满 7 天**：代码里没有 `actual_publish_date` 与今天的比较（前端只有提示文字 PromotionListPage.tsx:783, 1341）；
- 录完以后数字仍可被改：PATCH 收 `like_count`（promotion/schemas.py:126），采集器 `update_like_count` 也能写（promotion/service.py:1548-1577）。

建议：门槛改为「已结款 且 `get_today() ≥ actual_publish_date + 7`」，未满时 422 并告知可录日期；录完后三项只读（PATCH 与采集器拒绝，更正走带事件的接口或 admin）；`record_metrics` 不再推进 retro_status（B-05）。

全流程时间线——现有来源：

| 来源 | 覆盖 | 保留期 / 读取 | 缺陷 |
|---|---|---|---|
| audit_log（auth/models.py:233-260） | 建单、改单、发布、取消、召回、审核、寄回单号、7 天数据、复盘、附件绑定（promotion/service.py 各 `_audit.log`），结款单各动作（finance/service.py:177-186, 231-240, 423-434） | 超过 12 个月归档到 R2 并从库里删除（tasks/cleanup_tasks.py:49-104；core/config.py:127）；只能 `GET /api/audit-logs` + `auth.audit:read` 读（auth/api.py:384-387） | 不永久；粒度粗；金额刻意脱敏；审核不记驳回原因正文（promotion/service.py:1144-1159） |
| promotion_amount_log（promotion/models.py:415-487） | 三项金额的净变更 | 永久；字段级门控读取（promotion/service.py:1760-1777） | 只有金额 |
| urge_record（urge/models.py:157-，截图 177） | 催发留痕 + 截图 | 永久 | 只有催发 |
| blogger_retrospective（promotion/models.py:490-551） | 复盘各版本 | 永久 | 只有复盘 |
| 附件外键 | 收款码、品牌词、7 天数据、付款凭证 | — | 重传覆盖外键，旧图失联（promotion/service.py:559-560, 1733-1734） |
| 列上的时间戳 | cooperation_date、actual_publish_date、reviewed_at、metrics_recorded_at、retro_confirmed_at、settlement.payment_date | — | 召回 / 取消 / 重提没有时间字段；reviewed_* 只留最后一次 |

两种做法：

| 做法 | 代价 | 问题 |
|---|---|---|
| 从现有来源拼只读接口（audit_log + 上表各源） | S~M：一个查询接口 + 前端抽屉 | 12 个月后历史从库里消失，不满足「纠纷追溯」；audit 内容是各处随手写的 JSON，要逐个 action 翻译；驳回原因、重提说明、被替换的旧截图都拼不出来；读权限要另做字段级过滤 |
| **新建事件表（推荐）** | L：迁移 + 一个 helper 接入约 15 个状态动作 + `SettlementPaid` 监听 + 只读接口 + 前端时间线 + 测试 | 上线前的历史不回填（只能显示列上已有的时间戳）；以后每加一个动作都要记得写事件（用 helper + 测试兜住） |

推荐表 `promotion_event`：tenant_id、promotion_id、event_type、from_state、to_state、note、attachment_id（FK，每次重传的图各留一条）、actor_id、created_at；RLS forced；索引 (tenant_id, promotion_id, created_at)；只追加、不归档（不进 `archive_audit_logs`）。读接口 `GET /api/promotions/{id}/timeline` 合并 promotion_event + amount_log（沿用 `can_read_field("promotion", "quote_amount")` 门控）+ urge_record + blogger_retrospective；收款码与付款凭证的签名 URL 沿用现在的角色限制。「已完结终态：保存全流程时间线 + 截图」由「事件表永久保存 + 完结后单据只读（B-18）」满足，不需要在完结时另存快照。

### B11 已定：「品名 / `{商品简称}`」改用商品简称

所有用 `style_short_name_snapshot` 显示或计算的地方：

| 层 | 位置 | 用途 |
|---|---|---|
| 写入 | promotion/service.py:266, 277；importer/adapters/promotion.py:212 | 建单快照 = 款式简称，没有则款式全名（款式简称 264 个全空，所以实际都是款式全名） |
| 模型 / 索引 | promotion/models.py:108（String(128) NOT NULL）；trgm 索引 `idx_promotion_short_name_trgm`（alembic 006:217-222） | |
| 推广响应 | promotion/schemas.py:373；promotion/service.py:1988 | 推广列表、详情、仓库页共用这一份响应 |
| 列表搜索 | promotion/repository.py:692-699 | keyword ILIKE |
| 企微催发 | promotion/repository.py:186-227（第 208 行取快照）→ wecom/scan_service.py:91-96 → wecom/domain.py:65-84（白名单 wecom/domain.py:15；默认模板 wecom/template_service.py:15-20） | `{商品简称}` |
| 催发任务列表 / 详情 | urge/repository.py:24-45（第 31 行 `AS style_name`）→ urge/service.py:625-626 → UrgePage.tsx:218-232, 470-476 | 「款式」列第二行 |
| 博主 hover 历史合作 | negotiation/repository.py:152-175（第 158 行 `AS style_name`）→ BloggerHoverCard.tsx:104-110 | 款号下面的名字 |
| 前端 | PromotionListPage.tsx:664（「品名」列）、1119（改归属弹窗标题）；WarehousePage.tsx:85（「品名」列）；features/promotion/types.ts:59 | |

不在快照链路上、读款式表实时名的（不在已定范围，列出备查）：结款页「款式」（finance/service.py:643-675 `Style.style_name`）、谈款页（negotiation/repository.py:26）、发文进度卡片（report/repository.py:117-132）。

列表查询是否已 JOIN goods_main：是。`list_with_cte` 的 base CTE 有 `LEFT JOIN goods_main g ON g.id = p.goods_main_id`（promotion/repository.py:633-634），只选了 goods_code、is_suit（618-619）；单条路径（详情、状态推进后的返回）另查一次 goods_code、is_suit（promotion/service.py:1903-1917）。

改法：

1. 规则：显示名 = 商品简称（经 `promotion.goods_main_id` 实时 JOIN）；没填、或 `goods_main_id` 为空 → 回落建单快照。共享一条 SQL 片段（放在 promotion/repository.py，照 report 的 `GOODS_META_COLUMNS` 做法 report/advanced_repository.py:46-66），如 `COALESCE(NULLIF(BTRIM(g.short_name), ''), p.style_short_name_snapshot)`；单条路径用同规则的 Python helper。商品接口已把空串归一成 NULL（product/goods_schemas.py:27-29），但导入或手工 SQL 仍可能写入空串，NULLIF 兜一下。
2. 推广响应加 `display_short_name`（另带 `goods_title` 供悬停看全称）；**保留** `style_short_name_snapshot` 不动、不回填——快照是建单时的事实，商品简称以后还会改。
3. 前端：推广列表「品名」、改归属弹窗标题、仓库页「品名」改读 `display_short_name`，悬停显示商品全称（没有商品时显示快照）。
4. 企微：`find_urge_candidates` LEFT JOIN goods_main 选同一表达式，scan_service 改读它；变量名「商品简称」与白名单不动。说明：异常预警用的是 `goods_display_name`，回落的是商品全称（wecom/anomaly_service.py:153-154；product/goods_schemas.py:22-24）——催发回落快照是为了上线当天文案不变，两处回落对象不同是有意的。
5. 催发页 / hover 卡（Q8，推荐同批）：两处 SQL 各 LEFT JOIN goods_main 输出 `display_short_name`，前端改读，`style_name` 字段保留兼容。
6. 搜索：推荐加上商品简称（`g.short_name ILIKE :kw`，可再加 `goods_title`，与商品页一致 product/goods_repository.py:111-115）；不需要新索引；`idx_promotion_short_name_trgm` 保留（快照仍可搜）。
7. `goods_main_id` 为空的单：回落快照，行为与今天一致（§4 SQL-10 核数）。
8. 不需要迁移；不清空 `report_summary_coverage`（汇总表不存名称，alembic 056:12-15 同理）。
9. 测试：列表 / 详情 / 仓库筛选三条路径各覆盖「有简称 / 无简称 / goods_main_id 为空」；搜索命中商品简称；企微渲染（tests/integration/test_wecom_scan.py、tests/unit/test_wecom_domain.py）；催发列表（tests/integration/test_urge.py）；hover 历史。

## 3. 需业务确认的问题

| 编号 | 问题 | 推荐默认 | 理由 |
|---|---|---|---|
| Q1（B-02） | 「不合作」是否沿用「已取消」这个存储值，只在界面上显示成「已完结·不合作」？ | 是 | 图上「已完结」同时指两种终态，不能共用一个值；改值要动 6 处报表 SQL 并清汇总覆盖记录 |
| Q2（B-05） | 「已发布即可复盘」是否指：复盘文字发布后就能写、主管确认，不再等结款和 7 天数据？ | 是 | 这是 10-05 的新图，晚于 4b-1；4b-1「等结款定 ROI 分母」的理由在 10-02「博主卡片不算 ROI」之后已不成立。若指「主管审核不用等 7 天」，现在已经满足 |
| Q3（B-18） | 「已完结」需要哪些条件？ | 已结款 + 7 天数据已录 + 复盘已经主管确认，三者齐了系统自动完结，之后单据只读 | 改动 4 把复盘定为完结前的必经环节；只看 7 天数据会让复盘变成可选 |
| Q4（B-09、B-13） | 应付金额怎么算？ | 应付 = 博主服务费（置换恒 0；未发布就召回的单不付服务费）+ 寄回运费 + 主管追加的附加项；应付为 0 的单审核通过直接已结款 | 保住 PRD「置换不走财务」，同时让召回报销运费有路可走 |
| Q5（B-09） | 未发布就召回的单，终点是什么？ | 已结款后直接已完结，不录 7 天数据；发布后才召回的单照常录 | 没发笔记就没有 7 天数据，等「发布满 7 天」会永远卡住 |
| Q6（B-14） | 去掉结款单的「核查 / 驳回 / 填付款金额」，主管审核通过直达待财务付款，财务上传凭证时填实付金额？ | 是 | 图上写明单向；现在的第二级核查审的是同一份提交，且驳回后是死单 |
| Q7（B-12） | 寄拍是否改成「主管先通过，PR 补寄回单号后自动转财务」？ | 否，维持「先补单号再审核」 | 硬约束已满足，改了只省主管一次点击，却要多一个状态值 |
| Q8（B-22） | 催发页「款式」、博主 hover「历史合作款式」也改成商品简称？ | 是 | 读的是同一个快照，只改推广列表会出现同一单两个名字 |
| Q9（B-25） | 推广单审核权是否收回给主管独占？ | 是 | 图上节点是「主管审核」；现在任何 PR 都能审别人的单（只挡自审）。上线前先核对生产有没有 PR 审过别人的单 |
| Q10（B-15） | 结款日期 = 财务上传凭证当天（补传的会记成补传当天 / 当月）可以接受吗？ | 可以 | 图上写明系统生成；报表不用这个字段 |
| Q11（B-08） | 寄回运费是博主垫付、随结款报销给博主吗？ | 是 | 召回完成节点要求上传收款码，说明要给博主打款；如果有到付（公司直接付快递）的情况，录运费时要能标「不随结款支付」 |
| Q12（B-08） | 未发布就召回的单，寄回运费（以及送拍的样品成本）要不要进报表推广成本？现在报表只算已发布的单 | 交报表分支统一定口径，本分支不改报表 SQL | 报表口径属于投产 / 工作进度分支；本分支只保证数据录得进来 |

## 4. 迁移与存量数据影响

| 改动 | 迁移 | 对存量数据 |
|---|---|---|
| B-07 召回截图 | promotion 加 `recall_waybill_attachment_id`（FK attachment，ondelete RESTRICT） | 无 |
| B-16 已结款 | promotion 加 `settlement_chat_attachment_id`、`blogger_notified_at`；`settlement_status` 加值无 DDL（无 CHECK） | 存量置换「已付款」→「已结款」（数据 UPDATE）；存量非置换「已付款」推荐保持，由 PR 补传聊天截图（数量见 SQL-5） |
| B-18 已完结 | promotion 加 `completed_at` | 不回填（存量单没有完整证据） |
| B-19 时间线 | 新表 `promotion_event`（RLS forced，照既有 TenantScopedModel 与 rls 迁移写法） | 不回填 |
| B-14 结款单直达待财务付款 + 财务权限 | 数据：在途「待核查 / 待付款」结款单推进到「待财务付款」，或由业务逐单处理；「已驳回」的逐单处理；权限：从 finance 角色 revoke `settlement.review:approve`，并清权限缓存 | 数量见 SQL-3 |
| B-25 审核权限 | 新 scope（第一段不是 `promotion`）+ 授予 pr_manager / admin | 改变谁能审（SQL-9） |
| B-05 复盘解耦 | 无 DDL（retro 取值不变） | 存量「待复盘」的单照常可写复盘（SQL-6） |
| B-01、B-04、B-06、B-08~B-13、B-15、B-17、B-20~B-24 | 无 | 无 |

- 本分支没有任何改动触及汇总表的列或口径，**不需要清空 `report_summary_coverage`**（054 规则）。B-06 只改变以后召回单的 publish_status 写法，结果与今天「先取消再召回」一致，不算口径变化。
- 迁移编号从 057 起，需与其他分支协调；B 分支可以合成两个：057（四个新列 + `promotion_event`）、058（权限调整 + 在途结款单 / 置换已付款的数据处理）。

需要生产核对的只读 SQL（promotion / settlement 等是 RLS forced 表，请在租户上下文或 bypass 会话里执行，与以往生产核对方式一致）：

```sql
-- SQL-1 推广单状态分布
SELECT publish_status, recall_status, settlement_status, retro_status, cooperation_mode, COUNT(*)
FROM promotion WHERE is_active
GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC;

-- SQL-2 结款单状态分布
SELECT settlement_status, COUNT(*), SUM(total_amount) FROM settlement GROUP BY 1;

-- SQL-3 在途结款单与推广单状态（B-14 要处理的单）
SELECT s.settlement_no, s.settlement_status, s.created_at,
       p.internal_code, p.settlement_status AS promotion_status, p.cooperation_mode
FROM settlement s JOIN promotion p ON p.id = s.promotion_id
WHERE s.settlement_status <> '已付款'
ORDER BY s.created_at;

-- SQL-4 被驳回卡死的推广单（B-10 上线后可重提）
SELECT internal_code, cooperation_mode, review_reason_category, review_reason, reviewed_at
FROM promotion WHERE is_active AND settlement_status = '已驳回'
ORDER BY reviewed_at;

-- SQL-5 已付款推广单（加「已结款」后是否补聊天截图）
SELECT cooperation_mode, COUNT(*) AS paid,
       COUNT(*) FILTER (WHERE retro_status <> '未开始') AS in_retro
FROM promotion WHERE is_active AND settlement_status = '已付款'
GROUP BY 1;

-- SQL-6 复盘进行中的单（B-05 状态机改动的影响面）
SELECT retro_status, COUNT(*), COUNT(metrics_recorded_at) AS has_metrics
FROM promotion WHERE is_active GROUP BY 1;

-- SQL-7 召回单
SELECT publish_status, recall_status, settlement_status, COUNT(*),
       COUNT(return_shipping_fee) AS has_fee
FROM promotion WHERE is_active AND recall_status <> '未召回'
GROUP BY 1, 2, 3;

-- SQL-8 结款 / 审核相关权限的实际授予（角色 + 个人覆盖）
SELECT r.code AS role, p.scope
FROM role r
JOIN role_permission rp ON rp.role_id = r.id
JOIN permission p ON p.id = rp.permission_id
WHERE p.scope IN ('settlement.review:approve', 'settlement.pay:upload_proof', 'settlement:write',
                  'finance.settlement:pay', 'promotion.review:approve', 'promotion.*:*', 'promotion:read')
ORDER BY 1, 2;

SELECT u.username, p.scope, o.effect
FROM user_permission_override o
JOIN "user" u ON u.id = o.user_id
JOIN permission p ON p.id = o.permission_id
WHERE p.scope LIKE 'settlement%' OR p.scope LIKE 'promotion%' OR p.scope LIKE 'finance%';

-- SQL-9 不是主管 / 管理员审核的推广单（B-25）
SELECT p.internal_code, p.review_action, p.reviewed_at, u.username,
       array_agg(DISTINCT r.code) AS reviewer_roles
FROM promotion p
JOIN "user" u ON u.id = p.reviewed_by
JOIN user_role ur ON ur.user_id = u.id
JOIN role r ON r.id = ur.role_id
WHERE p.reviewed_by IS NOT NULL
GROUP BY 1, 2, 3, 4
HAVING NOT bool_or(r.code IN ('pr_manager', 'admin', 'platform_admin'));

-- SQL-10 品名改造覆盖面（B-20）
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE p.goods_main_id IS NULL) AS no_goods,
       COUNT(*) FILTER (WHERE NULLIF(BTRIM(g.short_name), '') IS NOT NULL) AS has_short_name
FROM promotion p LEFT JOIN goods_main g ON g.id = p.goods_main_id
WHERE p.is_active;

-- SQL-11 source_extra 里与 typed 列重复的键（B-24）
SELECT COUNT(*) FILTER (WHERE NULLIF(BTRIM(source_extra ->> '寄回单号'), '') IS NOT NULL) AS waybill_json,
       COUNT(*) FILTER (WHERE NULLIF(BTRIM(source_extra ->> '点赞数'), '') IS NOT NULL) AS like_json,
       COUNT(*) FILTER (WHERE NULLIF(BTRIM(source_extra ->> '收藏数'), '') IS NOT NULL) AS collect_json,
       COUNT(*) FILTER (WHERE NULLIF(BTRIM(source_extra ->> '评论数'), '') IS NOT NULL) AS comment_json
FROM promotion;

-- SQL-12 收款码覆盖（B-04 门槛的影响）与 audit 存量（时间线不回填的依据）
SELECT publish_status, cooperation_mode, COUNT(*), COUNT(payment_qr_attachment_id) AS has_qr
FROM promotion WHERE is_active GROUP BY 1, 2;

SELECT action, COUNT(*), MIN(created_at), MAX(created_at)
FROM audit_log WHERE resource IN ('promotion', 'settlement')
GROUP BY 1 ORDER BY 2 DESC;
```

## 5. 建议的实施顺序

1. **独立快速项**（无依赖、无迁移，可先上）：B-20 / B-21 / B-22 / B-23（品名，约 1.5 天）、B-24（删重复字段）、B-15（自动结款日期）、B-03 小修（发布日期不晚于今天）。
2. **地基**：B-19 事件表 + helper（后面每个新动作都要写事件；不做也能上后续项，但驳回原因与重提说明会被覆盖）；B-25 审核权限（Q9 确认后）。
3. **主管审核闭环**：B-10 重新提交 → B-11 财务可见信息（收款码与备注可先上，历史记录依赖 B-19）→ B-04 发布时收款码必填。
4. **结款链**：B-13 应付金额分流 → B-14 结款单直达待财务付款 + 财务权限 + 在途单处理 → B-16 已结款（聊天截图）。
5. **召回**：B-06 → B-07 → B-08 → B-09（依赖第 4 步的应付口径与已结款终点）。
6. **尾部**：B-05 复盘解耦 → B-17 7 天门槛（依赖 B-16）→ B-18 已完结 + 只读锁定（依赖 B-05、B-16、B-17）→ B-01 派生阶段与时间线界面（依赖以上全部字段）。
7. **可选**：B-12「待寄回单号」（Q7 选做时）。

## 附：本分支没有验证的内容

- 生产数据量与实际权限授予：都写成了 §4 的只读 SQL，结论里凡涉及「财务看不到」「主管和财务都能核查」的，依据是默认角色矩阵（auth/default_roles.py）与迁移 029，生产可能被手工调整过。
- 没有跑测试（本分支只读）；「哪些测试要改」来自 grep 与阅读，实施时以全量测试结果为准。
- 图 4 节点 1~5（仓库回填、建单继承、档期跟进、催发任务）不在本分支范围，只在 §2-B1 的映射表里列出对应字段。
