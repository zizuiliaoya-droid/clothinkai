# PRD V1.4 实施批次

依据 `LENNEA店铺系统 PRD V1.4.docx`（含 2026-09-25 新增 7 条改动）与 6 张导图，对照代码现状拆分的实施计划。

> 这份文件是执行台账。每批完成后更新状态与 PR 号，避免规划只存在于对话里。

## 已完成

| 批次 | 内容 | PR | main |
|---|---|---|---|
| 0 | bug 修复：safe_div 分母≤0、工作进度分母扣召回取消、刷单剔除失效（生产 46 条 ¥13,708）、add_cart_count 正则守卫、筛选记忆接三页、投产季节/类目多选、axios paramsSerializer | #3 | — |
| 1a | 商品分层建模：`goods_main` + `goods_style_item` + `platform_product.goods_main_id/channel` | #4 | 2c4dbf2 |
| 1a 修正 | 038 合并被 037 误拆的「一款多链接」商品（224→216） | #5 | 523e5ee |
| 1a 补数 | 039 补 36 条千牛 + 9 条万相台链接映射 | #6 | c0e7bee |
| 1a 建档 | 040 为 48 个只在日报出现过的款式建最小档案 | #7 | 110e237 |
| 1b | 投产报表从款式维度切到商品维度（覆盖率 47%→96%） | #8 | d84ebe6 |
| 1b 补数 | 041 清 Excel 单引号 + 建第一个真实套装 | #9 | 5b4d5f3 |
| 1b 口径 | 042 推广费归商品落库（`promotion.goods_main_id`），口径可纠正 | #10 | a23f566 |
| 1c-1 | 平台链接转运维视图（`ops.platform_link`），款式页撤千牛ID | #11 | 565d869 |
| 1c-2 | 商品/套装管理模块（`/api/goods` + `/goods` 页），清 `product.platform` 死权限 | #12 | 1b54f36 |
| 清理 | 删除从未接通的 bundle 模块（router 未注册、生产 0 行） | #13 | 5edff64 |

## 批次 2：合作模式 typed 化 + 三分支成本与流转

PRD 模块二的核心。**现状最大的问题是合作模式根本不是字段** —— 它存在 `promotion.source_extra` JSONB 的「合作方式」key 里，枚举只定义在前端 `PromotionListPage.tsx` 的 `SOURCE_FIELDS`，后端零校验。于是 PRD 里所有「后端必须做分支判断，不可只靠前端」的硬约束，一条都无法落地。

一个重要发现：**PRD 说的「主管审核」就是现在的 `PromotionService.review()`**，不是新环节。现有链路是 `发布 → 自动推进待核查 → 主管 review approve → 待付款 → 发 SettlementRequested → finance 建结款单`，PRD 要的只是在 approve 之后按合作模式走三个不同出口。所以这批是改造而非重写。

### 2a：合作模式字段 + 成本口径 ✅ 已完成（migration 046）

- `promotion.cooperation_mode` typed 字段 + `CooperationMode` 枚举，迁移时从 `source_extra['合作方式']` 回填并删掉 JSONB 副本（避免两处不同步）
- 字段可空：生产 5156 条里只有 2 条填了合作方式，5154 条历史「未发布」单没有这个信息。不编造默认值；service 允许从空补一次，补完即锁
- 单据已有模式后不可改：service 层拦截返回 409，不依赖前端禁用
- 新增 `return_shipping_fee`（寄回运费）
- `total_promo_cost` 用**数据库生成列**而非应用层重算 —— 写入路径有 HTTP / Excel 导入 / 迁移三条，手动重算迟早漏一条。配套给 `Promotion` 加 `eager_defaults`，否则 async session 访问生成列会抛 `MissingGreenlet`
- 成本三分支，后端强制覆盖前端传值：
  - 寄拍：`cost_snapshot = 0`
  - 送拍 / 置换：`cost_snapshot = SUM(goods_style_item.single_goods_cost WHERE is_active)`
  - 置换：`quote_amount = 0`
  - 区分了「建单初始化」与「更新兜底」两个方法：PRD 允许 PR 微调送拍/置换的样品成本，所以更新时只压那两个恒为 0 的字段，不重算汇总值覆盖人工录入
- 报表口径切到站外推广成本（`total_promo_cost`）共 5 处；`aggregate_promotion_summary` 的 4 处**保持** `quote_amount`，因为 PRD §9 定义「约篇金额 = 博主服务费合计」，与站外推广成本是两个口径
- 切口径时生产影响为 ¥0：报表只统计 `publish_status = '已发布'`，而生产只有 2 条已发布推广且样品成本为 0。趁数据少把口径改对
- 成本留痕**仍只记 `*_changed: true` 不记金额**：audit_log 的读取面只要 `auth.audit:read` 就能看全部，而金额本身受字段级权限保护（PR 看不到报价），写进 audit 等于绕过那层权限。金额级回溯应该做带权限的单据时间线表，不是放宽 audit 脱敏 —— 留到批次 4 跟催发留痕一起做

### 2b：三分支流转 + 驳回原因分类 + 修前端已坏的三处 ✅ 已完成（migration 047）

- `review()` 的 approve 分支按合作模式决定出口：
  - 寄拍 → 必须已上传「博主寄回衣服单号」才允许推进待付款，否则 422；单号从 `source_extra` 提成 typed 字段 `return_waybill`，并加了专门的上传端点
  - 送拍 → 待付款（现状行为）
  - 置换 → 直接「已付款」，**不发 `SettlementRequested`**。发了 finance 会建一张金额 0 的结款单，财务侧多出一堆不用处理的单子。状态机新增 `待核查 --approve_barter--> 已付款`
- 驳回原因分类：`review_reason_category` 枚举三选一必填，schema 层与 service 层双重校验 ← PRD 改动 5
- 合作日期 = 建单当天，服务端取 `get_today()`，请求里传的值不生效 ← PRD 改动 5
  - Excel 导入保留按文件日期落库，否则历史数据导不进来
  - 副作用：`test_sequence_resets_per_date` 原来通过 service 造两个不同日期的单，现在造不出来了。「按日期分别计数」的逻辑仍要守，所以那条断言降到仓储层直接验 `next_internal_sequence`
- **修三个现在就在坏的前端问题**：
  1. 「审核驳回」不传 `review_reason` → 改成弹窗填分类 + 说明
  2. 「审核通过」没有 disabled 条件 → 加 `settlement_status === "待核查"` 门槛
  3. 「取消」硬编码 `cancel_reason: "手动取消"` 且无确认弹窗 → 改成弹窗必填原因
- 召回流程接前端：列表加「召回」列，弹窗按当前状态给「发起召回 / 召回成功 / 召回失败」，失败可重新发起
- 列表新增「寄回单号」列，寄拍缺单号时显示橙色「待填」提示

未做（挪走）：
- 品牌词评论截图提交发布审核时必传 ← 涉及附件上传流程，与批次 4 的催发截图留痕共用一套，一起做

## 批次 3：谈款审核模块 ✅ 已完成（migration 048）

PRD 模块一，之前零实现（`谈款` / `negotiation` 全库 0 命中）。现有 `promotion.reviewed_by/review_action/review_reason` 已被结款审核占用（方向相反：那组字段记的是结款核查），所以谈款审核建新表而不是复用。

- 新表 `negotiation`：博主、对接 PR、款式、商品/套装、合作模式、平台、约定发布时间、博主服务费、审核意见、状态；配 RLS + 5 索引 + 4 CHECK
- 状态流转 草稿 → 待审核 → 审核通过 / 审核驳回，驳回可改后重新提交（重新提交会清掉上一轮意见，避免新单据挂着旧驳回理由）
- 置换模式博主服务费强制 0：前端置灰 + 后端覆盖传值，CHECK 约束兜底
- 审核通过自动生成推广单，完整携带合作模式
  - 「改状态 + 建推广单」必须同事务 —— CHECK 约束 `ck_negotiation_approved_has_promotion` 要求审核通过必有 `promotion_id`，两段提交会在中间态违反约束。做法是给 `PromotionService.create_promotion` 加 `autocommit` 参数复用建单逻辑，而不是在 negotiation 里重写一份
- **独立一级权限域 `negotiation`，不挂 `promotion.` 下**：`has()` 的前缀通配只看第一段，挂上去的话持 `promotion.*:*` 的 PR 会自动拿到审核权（既存的 `promotion.review:approve` 就有这个漏洞，目前只靠 `SelfReviewForbiddenError` 兜住自审）。同理也不给 PR `negotiation.*:*`
  - 授权矩阵：pr(read/write) / pr_manager(read/write/review) / finance(read) / operations(read)
- 博主 hover 卡新增「历史合作款式」（最近 N 次的款号 + 缩略图 + 合作时间 + 合作模式 + 发布状态）← PRD 改动 3
  - 数据取自推广单而非谈款单：草稿和被驳回的谈款没真推出去，不算历史合作
  - 只在真 hover 时才发请求（`enabled: open`），列表几十个博主不会一次打几十个接口

未做（挪走）：
- **hover 卡的「当时 ROI」**。博主维度 ROI 系统里从来没定义过 —— 现有 ROI 全是款式/商品维度；而且要先定「发布后多少天内算这次合作的效果」这个窗口，否则退款晚到会让历史数字自己变。先给已有口径的单赞成本（CPL）+ 点赞数，同样能看出这个博主推得怎么样。窗口口径定了之后再加快照字段（已决定走快照而非实时回算）

## 批次 4a：催发任务 ✅ 已完成（migration 049）

原规划把催发 + 复盘估成一批 4 人日。实际调研后拆开了，两个原因：

1. **原规划说「催发任务子模块」是新建，这不准确** —— U07 早就有一条「每天 09:00 扫描 →
   企微群发」的链路（`wecom/scan_service.py` + `urge_calculator.py` + `wecom_message` 表）。
2. **但那条链路在生产上从未产生过一条记录**：`wecom_config` 0 行、`wecom_contact` 0 行、
   `wecom_message` 0 行 —— 企微没配置，Beat 每天空转（租户列表为空直接返回）。

所以催发任务**刻意不建在 `wecom_message` 之上**：企微是通知方式之一，任务本身要能脱离它
成立。另外 `wecom_message` 是按 (blogger, pr) 聚合的、一条覆盖多个推广单，而 PRD 的
「博主确认发布 → 任务自动关闭」「超过 N 次提示主管」都是单据维度语义，塞不进聚合消息。

三张新表：
- `urge_config`（单租户单行）：临期天数 / 提示次数 / 超时上限 / 两个 urge_status 标签阈值 /
  自动开关。顺便**收编了 `legacy_settings.URGE_THRESHOLD_DAYS` 与
  `IMPORTANT_THRESHOLD_DAYS`** —— 这两个值在 `wecom/scan_service.py` 还被重复定义了一遍
  （`_URGE_DAYS` / `_IMPORTANT_DAYS`），双份真相谁改一边就不一致
- `urge_task`（一单一任务）：`UNIQUE(tenant_id, promotion_id)` 是自动扫描的幂等基石，
  走 `ON CONFLICT DO NOTHING` 而不是 `wecom/scan_service` 那种 SELECT-then-INSERT
  （后者真并发会双发）
- `urge_record`（一任务多条留痕）：截图 + 时间戳 + 备注，永久保留无 is_active

**`max_overdue_days`（默认 30）是防炸的，不是 PRD 要求的。** `find_urge_candidates` 把
urge_status='超时' 也算候选，而生产有 5134 条历史单的排期在半年前（diff -113 ~ -184 天）。
不设下界的话自动扫描第一次跑就建 5134 个任务，之后天天催。这些陈旧单仍可手动催。

**阈值从旧的 10 天统一到 PRD 的 5 天，对现有数据影响 0 条** —— 生产 5151 条有排期的单，
diff 只有 +90（17 条）和一堆负数，没有任何一条落在 0~90 之间。

其余实现要点：
- 自动扫描当天只计一次（`last_auto_urged_on IS DISTINCT FROM :today` 单语句原子）；
  手动催不受当日限制
- 新增 Beat 任务 `urge-task-scan`，00:30 UTC = 08:30 北京，排在企微投递（09:00 UTC）之前；
  **租户列表取自 `tenant` 而不是 `wecom_config`**，否则又跟着企微一起空转
- 扫描顺手收口陈旧任务（单据已发布/取消但任务还开着）—— 历史数据与将来漏调用的兜底
- 发布 / 取消时同事务关任务，异常被吞掉只记 warning：催发任务是辅助视图，不该让
  「发布推广单」这个主流程失败
- 「超过 N 次」由服务端算成 `over_limit` 返回，不让前端拿阈值自己比（阈值可配，
  前端各处比一遍迟早有地方忘了改）
- `attachment.ALLOWED_PURPOSES` 加 `urge_screenshot`；截图走后端代传 + 失败补偿删除
  R2 对象，与收款码同一套骨架
- 前端 `/urge-tasks`：看板四张卡 + 任务列表 + 时间线抽屉 + 阈值配置弹窗 + 按款式批量；
  推广管理的操作菜单加「催发」快捷入口

权限分两个一级域，这是故意的：
- `promotion.urge:read/write` 挂在 promotion 下**正是想要的** —— 催发是 PR 日常工作，
  PR 的 `promotion.*:*` 自动覆盖，运营的 `promotion.*:read` 自动拿到只读，
  仓库的 `promotion:read` + `promotion.warehouse:write` 两条都匹配不上所以看不到
- `urge_config:read/write` **必须独立** —— 叫 `promotion.urge_config:write` 的话
  PR 能自己把「催过 3 次提示主管」的阈值改成 999

## 批次 4b：复盘 + 品牌词截图 + 单据时间线（约 4 人日）

- 复盘环节 ← PRD 改动 4
  - 已结款 → 录 7 天数据 → **待复盘** → PR 手输复盘文字 → 主管确认 → 已完成
  - 复盘文字永久写入博主档案，跨单据沉淀，hover 卡按时间倒序展示全部历史复盘
  - 不拆结构化字段，就是一个多行文本框
  - 调研结论（影响设计）：
    - `settlement_status='已付款'` 是**终态**，没有现成后续状态可挂
    - **新开一个独立状态字段（第 4 个并行状态机）比扩 `settlement_status` 干净** ——
      扩进去的话所有按 `settlement_status='已付款'` 过滤的查询（索引、汇总、finance 列表）
      都会漏掉进入「待复盘」的单子
    - **置换单根本没有 settlement 行**（approve_barter 不发 SettlementRequested），
      所以复盘触发点不能挂在 finance 侧，否则置换单永远进不了复盘
    - 7 天数据目前只有 `like_count` 一个 typed 列，阅读/收藏/评论都在 `source_extra`
      JSONB 里无校验；也**没有「录入完成」标记字段**。触发点建议照 `set_return_waybill`
      的模式新开 `POST /promotions/{id}/metrics`，指标录入与状态推进同事务
    - 复盘文字**建子表**（照 `wecom_message` / `negotiation` 范式），不追加 JSONB ——
      全仓 JSONB 字段都是单次覆盖的快照，没有一处是 append 数组
- 品牌词评论截图提交发布审核时必传（从批次 2b 挪来）
  - 调研纠正：仓库里**不存在**「推广单发布截图」也不存在「品牌词评论截图」，
    现有图片上传只有 4 处（款式主图、推广收款码、拍单/刷单收款码、结算付款截图）。
    所以这是从零建，不是「复用已有附件流程」
- 单据时间线表（从批次 2a 挪来）：做金额级回溯，带权限，不放宽 audit 脱敏

## 批次 5：中间汇总表 + 投产/工作进度/BI（约 8 人日，最大一块）

PRD 模块四、五、六。5 张中间汇总表目前**全部不存在**，报表都是查询时现算。

- 5 张表：`product_roi_summary`、`pr_work_progress_summary`、`shop_daily_data`、`shop_week_summary`、`shop_month_summary`
- 定时任务每小时增量刷新最近 31 天，**禁止 truncate 全表**；历史区间靠页面手动刷新按钮
- `shop_daily_data` 每日凌晨 T+1 同步，保留手动重聚合
- `data_source` 标记来源（api 自动拉取 / excel 手动导入）；`仅退款率` 字段预留 API 回填
- 投产分析四个子菜单：单品投产列表 / 店铺日明细 / 店铺周汇总 / 店铺月度汇总，月→周→日下钻
- 单品投产页同时输出【自定义区间】+【最近 7 天】两套数据
- 工作进度表三部分：单品汇总卡片 / PR 人员进度明细 / 周周期明细，点击行弹窗跳详情，导出 Excel
- BI 看板 Tab1 店铺总览 + Tab2 单品分析，下钻携带全部筛选参数，导出图片，PR 权限隔离财务指标

## 批次 6：直播分账 + 全局安全（约 4 人日）

- 直播渠道完全分账 ← PRD 改动 1（`platform_product.channel` 字段 1a 已加，分账逻辑未做）
  - 直播 GMV 不计入店铺总销售额
  - 直播推广花费单独归集，直播投产独立计算
  - 店铺大盘只统计非直播；直播单开一个卡片
  - 单品投产列表里同商品有普通 + 直播链接时**拆两行**展示
  - 待确认：直播 GMV 的数据来源（千牛日报里有没有区分渠道的字段）
- 全页面水印 ← PRD 改动 6（工号 + 姓名 + 时间戳，斜 30°，透明度 10~15%，导出 Excel/PDF 同样带）
- 登录 IP 白名单 ← PRD 改动 7（白名单后台可配，失败写安全日志含 IP/时间/账号）
  - **需要防自锁**：配错白名单会把所有人关在外面，要留一个紧急通道（例如 platform_admin 豁免或环境变量开关）

## 踩过的坑（避免重复）

写 migration 与模型时反复撞到的几处，记下来省得再花时间：

- **`jsonb_build_object(:key, col)` 会抛 `IndeterminateDatatypeError`**。该函数入参声明是 `any`，PostgreSQL 推不出绑定参数的类型。必须写 `jsonb_build_object(CAST(:key AS text), col)`。046/047 的 downgrade 都栽在这里，是跑了一次真实 `alembic downgrade` 才发现的 —— 光看代码看不出来。
- **约束名会被命名约定套前缀**。`op.create_check_constraint("ck_promotion_xxx", ...)` 落库后实际叫 `ck_promotion_ck_promotion_xxx`。`op.drop_constraint` 用原名能对上（alembic 会套同样的约定），但按原名查 `pg_constraint` 查不到，验证脚本要注意。
- **数据库生成列在 async session 下会抛 `MissingGreenlet`**。SQLAlchemy 默认把 `Computed` 列当「稍后再取」，首次访问补发一条隐式 SELECT。要给模型加 `__mapper_args__ = {"eager_defaults": True}` 让 INSERT 带 RETURNING。
- **`INSERT ... SELECT ... WHERE NOT EXISTS` 同参数在两处会被推断成不同类型** → `AmbiguousParameterError`。改用 `ON CONFLICT DO NOTHING`。
- **`UPDATE ... FROM LATERAL` 引用不到 UPDATE 的目标表** → 用相关子查询。
- **类里有名为 `list` 的方法时，返回注解必须写 `builtins.list`**，否则会被解析成那个方法，类型检查静默失效。
- **模块级 `pytestmark = [..., pytest.mark.asyncio]` 会误伤同步测试** → 纯规则测试放 `tests/unit/`。
- **同一个绑定参数在一条语句里出现两次时，`::` 紧跟参数的那一处不会被替换**，留下字面量 `:today` 直接语法错误。`text()` 里一律写 `CAST(:x AS date)`。这和 046/047 的 `jsonb_build_object` 是同一个坑的两种表现，不限于那个函数。
- **`text("SELECT * FROM t").columns(*Model.__table__.columns)` 配 `scalar_one_or_none()` 返回的是第一列（id）而不是实体**，静默拿到一个 UUID，直到访问属性才炸。要取 ORM 实体就用 `select(Model)`。
- **`ruff format --check` 在 CI 里是独立一步**（`ruff check` 过了不代表 format 过），提交前要跑 `ruff format`。

## 待业务确认

这些挡在实施前面，需要业务方给口径：

1. **「当时 ROI」的统计窗口**（改动 3）：已定走**快照**（单据完结时存一次），不走实时回算 —— 回算会让退款晚到时历史数字自己变。还差一个口径：从实际发布日起算多少天内的销售算这次合作的效果（7 天？14 天？）。窗口定了才能加快照字段。批次 3 的 hover 卡先用已有口径的 CPL + 点赞数顶着。
2. **直播 GMV 数据来源**（改动 1）：千牛日报导出里有没有区分直播/普通渠道的字段？没有的话这部分分账拿不到数。
3. **催发阈值与次数**（改动 2）：默认 5 天 / 3 次，确认是否就用这个值起步。
4. **IP 白名单的紧急通道**（改动 7）：配错会全员锁死，需要定一个兜底方式。
5. **7 天点赞数**：现在 typed `like_count` 和 `source_extra['点赞数']` 两套并存、互不同步，要定哪个是准的。
6. **Excel 导出水印**（改动 6）：xlsx 没有原生水印层，通常做法是加页眉文字或铺一层浅色图片，需要确认接受哪种。

## 已知数据缺口（不挡实施，但影响报表完整度）

- 套装 `SUIT-1074568657697` 成本为空：两个成员款式（260415 / 260419）都没有 SKU 成本价
- 264 个商品里 66 个没挂平台链接，不会出现在任何销售数据里
- 千牛日报还有 20 条 ¥6,166 无法归属（千牛后台没填货号），清单在 `docs/data-gaps/千牛未填货号清单.csv`
- 40 个自动建档款式的类目是推断值，季节/SKU/成本/主图全空
- 历史推广 5154 条「未发布」保持现状（业务方决定，源数据本就没有发布日期/链接/点赞）
- **博主 hover 卡现在只能显示款号 + 日期 + 发布状态**，缩略图与点赞/单赞成本会全是「—」。生产实测：5156 条推广单 `like_count` 全为 NULL（只有 2 条已发布），264 个款式只有 2 个有主图。代码路径已验证（`calculate_cpl` 分母为 None/0 时返回 None，不会除零），缺的是数据 —— 要等 7 天数据回填 + 款式主图上传，这张卡才真正有参考价值。1636 个博主有合作历史，覆盖面本身是够的
