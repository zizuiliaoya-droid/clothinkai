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

## 批次 3：谈款审核模块（约 4 人日）

PRD 模块一，**当前零实现**（`谈款` / `negotiation` 全库 0 命中）。注意现有 `promotion.reviewed_by/review_action/review_reason` 已被结款审核占用，谈款审核要用新表而不是复用这组字段。

- 新表：谈款单（博主、对接PR、商品/套装、合作模式、约定发布时间、博主服务费、审核意见、状态）
- 状态流转：草稿 / 待审核 / 审核通过 / 审核驳回
- 置换模式博主服务费输入框置灰强制 0（后端同样校验，防绕过）
- 审核通过自动生成推广单，完整携带合作模式
- 权限：PR 新增/编辑草稿/提交审核；主管审核；财务只读
- 博主 hover 卡新增「历史合作款式」（最近 N 次合作的款号 + 缩略图 + 时间 + 当时 ROI）← PRD 改动 3
  - 「当时 ROI」口径待定：是记录快照还是实时回算，影响是否要加字段

## 批次 4：催发任务 + 复盘（约 4 人日）

- 催发任务子模块 ← PRD 改动 2
  - 手动随时发起（单条 + 按款式批量）
  - 自动按临期触发（距预定发布日 ≤ 5 天未发布，阈值后台可配）
  - 每次催发留痕：截图 + 时间戳 + 备注，一单多次，时间线倒序
  - 博主确认发布 → 任务自动关闭
  - 超过 N 次（默认 3，后台可配）提示主管考虑召回/转取消
  - 主管看板：本周已催发 N / 待催发 M / 超时未回 K
- 复盘环节 ← PRD 改动 4
  - 已结款 → 录 7 天数据 → **待复盘** → PR 手输复盘文字 → 主管确认 → 已完成
  - 复盘文字永久写入博主档案，跨单据沉淀，hover 卡按时间倒序展示全部历史复盘
  - 不拆结构化字段，就是一个多行文本框

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

## 待业务确认

这些挡在实施前面，需要业务方给口径：

1. **「当时 ROI」口径**（改动 3）：博主 hover 卡的历史合作款式要显示「当时 ROI」。是在单据完结时存一个快照，还是每次实时回算当时区间？前者要加字段，后者数字会随后续数据变化。
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
