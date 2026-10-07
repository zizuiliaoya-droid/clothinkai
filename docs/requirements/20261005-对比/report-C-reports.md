# 调研分支 C 报告：投产报表 / 工作进度表 / BI 看板（图 1~3）

> 基线 `main@a0f1410`（alembic head `056_goods_short_name`）。只读调研：除本文件外未改任何文件，未连生产。
> 证据写成 `路径:行号`。路径省略前缀时：裸文件名（如 `advanced_repository.py`）指 `backend/app/modules/report/`；
> `collect/`、`importer/`、`promotion/`、`product/`、`auth/` 开头指 `backend/app/modules/` 下对应目录；`core/` 指 `backend/app/core/`；
> `tasks/` 指 `backend/app/tasks/`；`0xx_*.py` 指 `backend/alembic/versions/`；`pages/`、`components/`、`features/`、`App.tsx` 与
> `*Page.tsx` 指 `frontend/src/`（页面在 `pages/` 下）。PRD 原文引自 `docs/requirements/LENNEA店铺系统 PRD V1.4.docx`（按章节号）。

## 0. 结论

图 1~3 不是新需求。`BI看板.png`、`工作进度表.png` 与 PRD 原有的 `docs/requirements/导图5.png`、`导图4.png`
逐字节相同（SHA256 前缀 `EE03B867A35F246B` / `F8A446B70D3C6CC7`），`投产报表.jpg` 逐节点等于 PRD V1.4 §4 的
Mermaid。三张图重申的就是台账「批次 5b-2（待定）」：汇总层底座（每小时 31 天、先删后插、覆盖记录、新鲜度、
导入后自动刷新）已在 5b-1 / 1c / 1d / 1e 落地并满足；缺的是视图层（店铺周/月子菜单与下钻、工作进度三块、
BI 两个 Tab 与下钻）、几组 PRD 公式和权限；另有 3 处需要业务拍板的冲突。

逐节点共 43 条：**已满足 11 / 部分满足 12 / 缺失 17 / 冲突 3**。按推荐默认答案粗估约 20 人日（含测试，不含批次 6）。

最要紧的几件事：

1. **汇总表要补列 + 加商品维度**，才能支撑 BI 切汇总、工作进度单品卡片与季节筛选、店铺周/月公式。建议**一个
   migration 补齐、只清一次覆盖记录**，并和批次 6 直播分账的 channel 维度一起设计（C-14 / C-23 / C-33，§4）。
2. **现有缺陷**：千牛 / 万相台 extra 里的比率列、累计列被直接求和（例：「商品详情页跳出率 78.15%」按商品逐日相加），
   店铺数据页和投产报表都会显示无意义的数，违反 PRD「聚合比率总和重算」（C-11）。
3. **现有缺陷**：「推广单件成交成本」实现公式与 PRD 不同且恒为空（C-05）；套装商品编码是 `SUIT-<千牛ID>`，
   投产 / BI 页直接展示，等于外露千牛 ID（C-01，默认改为报表页不显示编码，不动存量编码）。
4. **权限**：`pr` 只有发文进度表的读权限，`finance` 没有任何报表权限，PRD 要的「PR 看本人 / 财务只读导出 / 财务看 BI 全部」做不到；
   `pr_manager` 没有 `product:read`，投产页的季节/类目下拉很可能拉不到选项（C-32 / C-41 / C-02，需生产核对）。
5. **下钻与「跳单据详情」的公共前提不存在**：前端没有任何页面从 URL 恢复筛选，也没有推广单详情路由或只读详情视图
   （C-28 / C-36 / C-37）。

## 1. 主表

判断取值：已满足 / 部分满足 / 缺失 / 冲突。工作量：S ≤ 0.5 天、M 1~2 天、L ≥ 3 天。

### 图 3 投产报表

| 编号 | 图·节点 | 需求（摘） | 现状（证据） | 判断 | 建议改动 | 工作量 | 依赖 / 风险 |
|---|---|---|---|---|---|---|---|
| C-01 | 图3 子菜单1（图2 卡片、图1 Tab2 同） | 前端展示商品名称（PR #29 起=简称），千牛商品ID后台隐藏 | 简称✓ `pages/ProductionPage.tsx:192-200`、`pages/BiDashboardPage.tsx:193-201`；extra 跳过 商品ID/主商品ID/主体ID `production_service.py:27-41`。✗「商品编码」列直接显示 `goods_code`（`ProductionPage.tsx:177-191`、`BiDashboardPage.tsx:178-192`，后端图表标签 `bi_service.py:118,129`），而套装编码是 `SUIT-<千牛ID>`、同款多链接第 2 条起是 `<货号>-<千牛ID>`（`backend/alembic/versions/037_backfill_goods_main.py:13-15,103,186-187`），商品页新建时的提示还在建议这种写法（`pages/GoodsPage.tsx:449-453`）。同一编码也出现在推广列表、谈款列表、企微异常预警里（`promotion/repository.py:617-620`、`backend/app/modules/negotiation/repository.py:27`、`backend/app/modules/wecom/anomaly_service.py:157`） | 部分满足 | 默认：报表页（投产、BI、工作进度卡片）不显示商品编码，改为「简称 + 含款号」；商品页提示改成不含千牛 ID 的写法（如 `SUIT-<首个成员货号>`）。若要求所有页面都不出现，需一次性数据迁移改码，这会推翻「商品编码建档后不可改」的设计（`product/goods_service.py:5`、`product/goods_schemas.py:118-121`）→ Q2 | S | SQL-1 量一下涉及多少商品；数据库内引用都走 `goods_main_id`，改码技术上安全，但历史审计 / 企微消息里是旧编码 |
| C-02 | 图3 子菜单1 | 季节多选 + 筛选记忆 | 后端多选 `advanced_api.py:146-147` → `advanced_repository.py:87-102`；记忆 `product_roi` 存 preset/season/category/exclude_brushing，自定义日期区间不记（`ProductionPage.tsx:55-61,86-113`） | 已满足 | 风险项：选项来自 `GET /api/dict-items`，要 `product:read`（`backend/app/modules/product/dict_api.py:49`），默认 `pr_manager` 无 `product.*`（`backend/app/modules/auth/default_roles.py:159-190`）→ 主管的下拉可能为空。建议出一个报表侧的季节/类目选项接口（与 C-32 一起） | S | SQL-5 |
| C-03 | 图3 Z1 入参 `stat_type: custom / week_7`；子菜单1「同时输出自定义区间 + 7 天两套数据」 | 10-02 第一版：同一行后加「近 7 天」支付金额、推广总成本、净投产比 | `ProductionReport` 只有 items + previous（`advanced_schemas.py:119-121`）；previous（环比）每次都算但前端不显示（`production_service.py:129-147`；`ProductionPage.tsx:321` 只用 items） | 缺失 | `get_report` 再按近 7 天区间调一次 `_goods_rows`（同一份 SQL，汇总/实时各自判断，同本期/上期现有做法），按 goods_id 合并成 `recent7_pay_amount / recent7_total_spend / recent7_net_roi`；前端加 3 列、列头注明区间；导出加 3 列。**不新增 stat_type 列**（§2-C1） | M | Q1（终点日） |
| C-04 | 图3 按 tb_item_id 关联多源数据 | 千牛 / 万相台 / 推广 / 刷单按商品归集 | `qn_link` CTE + 等值 JOIN（`advanced_repository.py:121-163,301-416`）；推广按 `promotion.goods_main_id`、为空回落主商品（`advanced_repository.py:378-398`） | 已满足 | — | — | — |
| C-05 | 图3 Z3 执行全部公式计算 | PRD §9：退货前投产比=支付÷总推广成本；净投产比=(支付−成功退款)÷总推广成本；推广单件成交成本=总推广成本÷支付件数 | 净投产比✓（`backend/app/services/metric/style_roi.py:30-43`，SQL 侧 `advanced_repository.py:589-591`）。退货前投产比无。推广单件成交成本实现为「加购成本÷加购转化率÷(1−退货率)」，转化率恒传 None → 恒为空（`backend/app/services/metric/style_roi.py:46-59`、`production_service.py:275-280`）。汇总表无支付件数（`summary_models.py:60-98`、`summary_refresh_service.py:85-92`）。另：「待确认收货金额」列其实是 支付−退款（`ProductionPage.tsx:210`），PRD 叫「实际销售金额」，规则 9 明确待确认收货不参与扣减 | 部分满足 | 汇总表补 `pay_orders`；单件成交成本按 PRD 改；加「退货前投产比」；列名改「实际销售金额」 | S（随迁移） | Q3；该列会从「—」变成有数 |
| C-06 | 图3 Z3「站外来的加购负数置0」、备注2 | 站外加购=全渠道加购−站内加购，<0 取 0（估算口径）；站内加购成本=站内投放÷站内加购 | 全渠道加购=`qianniu_daily.add_cart_count`（055 typed，`backend/app/modules/collect/models.py:142-148`）。站内加购：`ad_daily` typed 只有 cost/impressions/clicks/gmv（`collect/models.py:164-191`），adapter 默认列不含购物车（`backend/app/modules/importer/adapters/wanxiangtai.py:25-32`），其余列原样进 extra（`importer/adapters/wanxiangtai.py:90-95`）。真实导出键名见 §2-C3；单测样例只有 6 个默认列（`backend/tests/unit/test_crawler_adapters.py:118-126`） | 缺失 | `ad_daily.add_cart_count` typed（取「总购物车数」，宽松解析同 055）+ 回填存量；`product_roi_summary.ad_add_cart_count`；读取侧按区间**先求和、再相减、再取 0**；站内加购成本=`safe_div(站内投放, Σ站内加购)`；新解析必须有「从真实导出行走 adapter」的测试 | M | 逐日取 0 再求和 ≠ 区间取 0，必须在读取侧算、不落盘；SQL-4 |
| C-07 | 图3 总的加购数量 / 总加购成本 | 总加购=全渠道加购件数；总加购成本=(站内+站外)÷总加购 | `_ADD_CART_SUM`（`advanced_repository.py:42,352`）；`add_to_cart_cost=总花费÷总加购`（`backend/app/services/metric/style_roi.py:23-27`、`production_service.py:273`）；汇总表已存（`summary_models.py:84`） | 已满足 | — | — | — |
| C-08 | 图3 数据源「商品两张表 修改货品成本价」 | PRD §9 毛利：销售货品总成本=支付件数×单品货品成本价；单品毛利；单品利润率 | 投产没有任何指标用到 `goods_style_item.single_goods_cost`；改商品成本（`backend/app/modules/product/goods_service.py:116-128`）不触发任何刷新（刷新入口只有 Beat `backend/app/core/celery_app.py:136-141`、导入 `backend/app/tasks/import_tasks.py:202,236-253`、手动端点 `advanced_api.py:219-256`） | 缺失 | 读取时现算：销售货品总成本=Σ支付件数×当前单品成本（启用成员 `single_goods_cost` 之和），毛利/利润率走 safe_div；成本缺失（如套装 SUIT-1074568657697）显示空、不当 0。现算 → 改价立即生效、不需要刷新（与商品简称同一取向） | M | 依赖 C-05 的 pay_orders；Q4 |
| C-09 | 图3/图4 样品成本 | 推广单成本是建单快照，改货品成本不回溯 | 送拍/置换建单时 SUM 启用成员成本（`backend/app/modules/promotion/repository.py:322-341`）存 `cost_snapshot`（`promotion/models.py:110`），之后只能 PR 微调 + 金额时间线（台账 4b-2） | 已满足 | — | — | — |
| C-10 | 图3 分母为 0 置 NULL | 禁止除零 | `safe_div`（`backend/app/services/metric/common.py:12-34`）；SQL `CASE WHEN total_spend <= 0 THEN NULL`（`advanced_repository.py:589-591`） | 已满足 | — | — | — |
| C-11 | 图3 备注4 | 聚合比率用总和重算，禁止 avg 单行比率 | 核心指标都是先求和再除（`production_service.py:238-281`、`work_progress_service.py:68-101`、`bi_service.py:151-168`）✓。extra 数值列不分可加与否一律相加：店铺按日跨商品求和（`store_daily_service.py:94-105`）、投产按商品跨天求和（`production_service.py:211-235`）、店铺页周/月再相加（`pages/StoreDailyPage.tsx:76-89`）、导出周/月相加（`export_service.py:246-251`）。受害列见 §2-C2 | 部分满足 | 一处维护「不可加列」清单（比率 / 均值 / 累计 / 评分），汇总时不求和（显示「—」）；常用的几个按分子分母重算（如 点击率=Σ点击÷Σ展现）；页面与导出共用 | S | SQL-6 可看到现象 |
| C-12 | 图3 子菜单 2/3/4，P4→P3→P2 下钻 | 店铺日明细 / 周汇总 / 月度汇总三个子菜单，月→周→日下钻 | 菜单「报表与分析」只有 店铺数据 / 投产报表 / BI看板（`components/AppLayout/AppLayout.tsx:141-150`）；`StoreDailyPage` 一页 + 日/周/月/年下拉，周月在前端把日行相加（`StoreDailyPage.tsx:21-26,63-95`）；无下钻；前端全库无 `useSearchParams` / `URLSearchParams` / `location.search`。`DailyDataPage` 是「数据管理 → 千牛数据 / 单品站内推广」的原始导入明细表（`App.tsx:166-217`），不是店铺报表 | 部分满足 | 菜单改为「投产分析」：单品投产列表 / 店铺日明细 / 店铺周汇总 / 店铺月度汇总；周、月页读 C-13 的路径；行点击带 `?month=` / `?week_start=` 进下一层；店铺页接筛选记忆（白名单已有 `store_daily`，`user_preference_service.py:21-28`，页面没用） | M | 依赖 C-13 / C-14 |
| C-13 | 图3 WEEK / MONTH → 投产分析菜单 | 周/月汇总表供菜单读取 | 两表只在刷新时写（`summary_refresh_service.py:233-236,370-435`），没有任何读取路径（`summary_read.py` 只读 `shop_daily_summary`，`summary_read.py:296-320`）；导出的周/月在 Python 里分桶（`export_service.py:222-267`） | 缺失 | `SummaryReadRepository` 加 `store_week / store_month`；覆盖判断按完整桶区间（含今天的周/月覆盖不全 → 实时，同工作进度「当月实时」）；实时路径 = `StoreDailyRepository.aggregate` 按 `bucket_expr` 分桶；补等值测试（JSON 比对 + 拦截 `record_source` + 场景有效性） | M | 店铺实时查询实测 0.7ms，回退实时没有性能问题 |
| C-14 | 图3 店铺日/周/月报表（PRD §9） | 实际销售额=支付−退款−刷单本金；推广总额=站内+站外；推广总占比 / 站内占比 / 站外占比 / 净推广占比；净投产；投产（扣除刷单） | 店铺行只有 访客 / 支付金额 / 支付件数（页面标题误写「支付订单数」，`collect/models.py:136-137`、`StoreDailyPage.tsx:118`）+ 3 个手填广告消耗（`advanced_repository.py:264-295`、`advanced_schemas.py:70-79`）；店铺汇总表只有这 3 个指标（`summary_models.py:151-165`、`summary_refresh_service.py:114`）；`backend/app/services/metric/store_daily.py:1-8` 是空占位 | 缺失 | 在店铺汇总的唯一刷新源 `StoreDailyRepository.aggregate` 里补全店口径的 退款、刷单本金、站内花费（全部 ad_daily）、站外推广成本（全部已发布推广单）；三张店铺汇总表补同名列；比率全部读取侧 safe_div | M | 依赖迁移；Q5 / Q6；批次 6 要分直播 |
| C-15 | 图3「千牛API 店铺日数据 含仅退款率」、备注3 | 仅退款率=仅退款金额÷支付金额；一期手录，以后 API 回填 | `store_daily` 只有 3 个广告消耗 + 备注（`work_progress_models.py:57-68`）；PUT `/api/reports/store-daily/{day}` 要 `report.store_daily:write`（`advanced_api.py:111-128`），默认只有 admin 持有；`upsert_manual` 过滤掉 None，值清不回空（`store_daily_service.py:133-135`）；前端无调用方（全库无 PUT store-daily）；仅退款率无任何展示 | 缺失 | `store_daily.refund_only_amount numeric(12,2)`（按天存金额）；日明细加录入（行内编辑或弹窗）；`upsert_manual` 改按 `model_fields_set` 判断（PR #29 同一个坑）；日/周/月/BI 读取时 `Σ仅退款÷Σ支付`；`store_daily` 是读取时 LEFT JOIN 的 override 层，不进汇总表、不需要清覆盖 | M | Q7 / Q8；`aggregate` 以千牛日报为主表，没有日报的日子手填值显示不出来（`advanced_repository.py:277-281`） |
| C-16 | 图3 三个「Excel 手动导入（备用一期）」 | 生意参谋商品 / 站内推广 / 店铺日数据 | 前两条有：`qianniu`（`importer/adapters/qianniu.py:99-103`）、`wanxiangtai`（`importer/adapters/wanxiangtai.py:65-69`），入口 `/qianniu`、`/ad-data`（`App.tsx:166-217`）。店铺日数据没有导入源（已注册 9 个：qianniu / wanxiangtai / manual_promotion / manual_blogger / manual_settlement / manual_style_sku / huitun / manual_tao_order / manual_brush_order，`importer/adapters/order_adjustment.py:214-217` 等）。店铺报表=商品日报求和，店铺「访客数」是商品访客数之和（`advanced_repository.py:271`），同一访客看多款会重复计 | 部分满足 | 一期维持「商品日报汇总 + 手填层」，店铺页「访客数」改名「商品访客数合计」；业务能给生意参谋「店铺整体」日报导出再加 `qianniu_shop` 通道写 `store_daily` | S | Q9 |
| C-17 | 图3 data_source | 标记 api 自动拉取 / excel 手动导入 | `qianniu_daily` / `ad_daily` 无来源列、无 import_batch_id（`collect/models.py:122-191`）；adapter 返回随机 UUID 而非行 id（`importer/adapters/qianniu.py:194`、`importer/adapters/wanxiangtai.py:149`）；来源只记在批次上（`importer/models.py:45`） | 缺失 | 两张日报表 + `store_daily` 加 `data_source varchar(8) NOT NULL DEFAULT 'excel'`；adapter 按批次来源写（采集器批次=api）；存量 348 行按台账结论即为 excel。可推迟到接 API 时一起做 | S | 现在没有报表消费它 |
| C-18 | 图1/2/3 定时任务 | 每小时，仅刷新最近 31 天 | `crontab(minute=20)`（`core/celery_app.py:136-141`）；窗口 31 天（`backend/app/tasks/summary_tasks.py:56,70-99`）；导入后只刷已覆盖或窗口内的日子（`tasks/summary_tasks.py:209-246`） | 已满足 | — | — | — |
| C-19 | 图2/3 增量更新 | 先删后插，禁止 truncate | `_replace` 只删区间内的行（`summary_refresh_service.py:440-463`）；租户级 advisory lock（`summary_refresh_service.py:119,211-214`） | 已满足 | — | — | — |
| C-20 | 图2/3 页面手动刷新按钮 | 可重算任意历史区间（31 天外只能手动） | 端点接受任意 ≤366 天区间、上沿截到今天（`advanced_api.py:219-256`、`domain.py:14,40-45`）；按钮只在读汇总表时出现（`components/ReportFreshness/ReportFreshness.tsx:113-152`），没覆盖的区间走实时（`summary_read.py:134-147`） | 部分满足（等效） | 维持现状（§2-C7）。业务坚持「任何区间都有按钮」的最小改法：实时状态也显示按钮 + 二次确认「刷新后这段改读汇总表，31 天外以后只能手动刷新」 | S | 刷新权限只授 admin（`advanced_permissions.py:20-22`），Q21 |
| C-21 | 图3 店铺后台定时聚合 每日凌晨 T+1 | 店铺周/月每日聚合 | 店铺日/周/月随每小时任务刷新，且扩到完整周/月桶（`summary_refresh_service.py:164-184,217-236`） | 已满足（更频繁） | — | — | PRD 的「T+1 同步」针对千牛 API，API 未接 |

### 图 2 工作进度表

| 编号 | 图·节点 | 需求（摘） | 现状（证据） | 判断 | 建议改动 | 工作量 | 依赖 / 风险 |
|---|---|---|---|---|---|---|---|
| C-22 | 图2 来源 A/B：谈款审核单据表 + 推广管理单据表 | 单据来自谈款 & 推广管理，无需重复录入 | 工作进度只读 `promotion`（`advanced_repository.py:170-227`）；约稿量=合作日期落在区间内的有效推广单数，含已取消/召回（`advanced_repository.py:185,210-211`）；谈款表没进任何报表 | 部分满足 | 约稿量口径不变；可选加「谈款中」计数（读 negotiation） | S | 分支 A（图 5「已定稿才生成推广单」）会让约稿量变少、合作日期后移到定稿日（合作日期=建单当天，台账 2b）；Q14 |
| C-23 | 图2 Z1 按 tb_item_id + PR + 时间周期聚合 | 商品 × PR × 周期 | `pr_work_progress_summary` 粒度 (PR, 日)，唯一索引 `(tenant_id, pr_id, stat_date) NULLS NOT DISTINCT`（`backend/alembic/versions/052_report_summary_tables.py:176-182`；`summary_models.py:101-148`）；刷新逐日调 `aggregate_by_pr`（`summary_refresh_service.py:291-333`）；无商品维度 | 部分满足 | 加可空 `goods_main_id` 维度，唯一索引用原生 DDL 重建为 `(tenant_id, pr_id, goods_main_id, stat_date) NULLS NOT DISTINCT`（ORM 侧继续不声明）；`aggregate_by_pr` 泛化出按商品分组（同 `daily_trend_by_goods(goods_id=None)` 的做法），商品归属用与投产相同的「`goods_main_id` 为空回落主商品」；PR 表读取时 SUM 掉商品维度，与现状逐字相等（§2-C8） | M | 迁移清覆盖；刷新仍是 31 次查询，耗时基本不变；SQL-3 |
| C-24 | 图2 Z2 执行全部指标计算 | 完成率、超时率、各类成本 + PRD §9 工作进度公式：发布单篇均价、发货发布率、金额发布率、总金额发布率、加购成本（人数 / 件数） | 14 个计数 + 6 个比率（`advanced_schemas.py:15-38`、`work_progress_service.py:68-101`），超时率/完成率分母已扣召回与取消 ✓。PRD 那 6 个公式都没有；汇总表没有金额列，只有 `cost`=SUM(cost_snapshot)（`advanced_repository.py:207`），所以「成本(含衣服)」实际只含样品成本（`pages/WorkProgressPage.tsx:82-87`） | 部分满足 | 汇总表补 quote_amount / published_quote_amount / unpublished_quote_amount / cancelled_quote_amount / recall_quote_amount / promo_cost（total_promo_cost）/ pending_count；读取侧算 发布单篇均价、金额发布率、总金额发布率；发货发布率要等发货单号 typed（分支 A/B）；加购成本（人数/件数）一期不做 | M | Q12 / Q15 |
| C-25 | 图2 分母 ≤0 置 NULL | 分母为 0 单元格置空 | 全部比率走 `safe_div`（`work_progress_service.py:84-100`） | 已满足 | — | — | — |
| C-26 | 图2 F1 顶部单品汇总卡片 | 当季前 12 个商品（按约稿量）+ 季度锁定；每卡 约稿量 / 发布量 / 完成率 / 超时量 / 点赞数 / 推广成本；「查看全部」；显示商品名 | 页面只有 PR 一张表（`WorkProgressPage.tsx:96-139`）。相近的现成东西：发文进度表的款式卡片，按约篇量降序、每页 12（`pages/PublishProgressPage.tsx:37-41`；`repository.py:91-142`），但维度是款式、成本是 cost_snapshot、无季节 | 缺失 | 新端点 `GET /api/reports/work-progress/goods?<区间>&season=…&limit=12`（读 C-23 的商品维度，约稿量降序 + 商品编码兜底）；前端 12 张卡 + 「查看全部」弹窗表；名称按简称规则；推广成本用 promo_cost | M | 依赖 C-23；生产 264 个商品只有 2 个填了季节，选季节后卡片基本为空；Q10 / Q11 |
| C-27 | 图2 F4「季节多选 + 筛选记忆」vs 10-03「手选一个季节并锁住」 | — | 工作进度现在没有季节筛选，记忆只存 `{month}`（`WorkProgressPage.tsx:20-35`） | 冲突（可调和） | 推荐合一：页面一个季节多选，存进 `pr_work_progress` 记忆就是「锁定」；卡片、PR 表、周表、导出共用 | S | Q10 |
| C-28 | 图2 F2 PR 人员进度明细表 | 点 PR 行 → 弹窗单据列表 → 跳单据详情 | 表格不可点（`WorkProgressPage.tsx:130-138`）；推广列表接口支持 `pr_id` + 合作日期区间（`backend/app/modules/promotion/api.py:98-100`、`promotion/repository.py:669-677`），但「未分配」（pr_id 为空）筛不出、不能按商品/季节筛（`promotion/repository.py:52-76` 无 goods 字段）；前端只有 `/promotions` 列表路由（`App.tsx:155`），列表页只有各动作弹窗和金额记录抽屉（`pages/PromotionListPage.tsx:1570-1582`），没有只读详情视图 | 缺失 | 推广列表接口加 `goods_main_id` / `seasons` / 未分配 筛选；工作进度行点击 → 弹窗（复用列表接口）；新增 `/promotions?id=<uuid>` 打开只读详情抽屉（`GET /api/promotions/{id}`，`promotion/api.py:137-147`） | M | 财务没有 promotion 读权限（`auth/default_roles.py:192-210`），弹窗会 403；详情抽屉与 C-36 / C-37 共用 |
| C-29 | 图2 F3 周期周明细表 | 点击周期，展开单据明细 | 没有按周的工作进度；ISO 周（周一开始）分桶已统一（`advanced_repository.py:68-84`、`summary_refresh_service.py:142-152`） | 缺失 | 从汇总表按 `date_trunc('week', stat_date)` 读出周行（不需要新表），首尾周按筛选区间截断并标出实际日期；展开行调推广列表接口（合作日期=该周） | M | Q13（按合作日期还是预定发布日期分周） |
| C-30 | 图2 F4 筛选 + 导出 Excel | 季节多选 + 筛选记忆 + 导出 | 只有月份选择（`WorkProgressPage.tsx:113-121`）；导出 20 列 PR 表，只取 `time_range[0]` 所在月（`export_service.py:27-48,269-296`），无季节；权限 `report.export:read`（`export_api.py:22-25`） | 部分满足 | 时间筛选换成通用 `ReportTimeRangeFilter`（BI 下钻要带日期区间）+ 季节；导出按区间 + 季节，加「单品卡片」「周明细」两个 sheet；水印与导出权限随批次 6 | S | 批次 6 要把导出改成独立 action |
| C-31 | 图2 「小红书/千牛API 笔记效果数据 点赞、访客、加购」+「Excel导入 笔记效果数据 一期兜底」 | — | 点赞/收藏/评论是 typed + 录入时间（`promotion/models.py:152-156,189`），走 7 天数据录入且**截图必传**（台账 4b-1，PRD 改动 4 原文）；没有笔记访客/加购字段；推广单导入 INSERT-only、不含任何效果列、不能更新已有单（`importer/adapters/promotion.py:1-12,40-55`）；当前没有报表用到笔记访客/加购 | 冲突 | 一期不做笔记效果 Excel 导入；将来的导入/API 只写访客、加购（新字段），点赞/收藏/评论仍走截图录入；或导入值不写 `metrics_recorded_at`（不计算单篇点赞成本） | S | Q16 |
| C-32 | 图2 / PRD §5 权限 | PR 仅看本人；主管、运营看全部；财务只读导出 | `pr` 只有 `report.publish_progress:read`（`auth/default_roles.py:141-157`），工作进度接口要 `report.work_progress:read`（`advanced_api.py:49-53`）→ 403，但菜单照样显示（`AppLayout.tsx:114-117`）；`aggregate_by_pr` 没有按人过滤的参数（`advanced_repository.py:170-227`）；`finance` 没有任何 report 权限（`auth/default_roles.py:192-210`） | 缺失 | 新 scope `report.work_progress:read_own` 授 `pr`（action 不叫 read，免得被 `report.*:read` 通配捞走，`has()` 见 `backend/app/core/security/permissions.py:39-53`）；端点：有 `read` 看全部、只有 `read_own` 时强制 `pr_id=当前用户`（汇总 / 实时 / 卡片 / 周表 / 弹窗都加）；给 finance 显式授 `report.work_progress:read` + 导出权限；季节选项走报表侧接口（PR 没有 product 读）；照 `backend/tests/unit/test_summary_permission_boundary.py` 加权限边界单测 | M | 发文进度表现在把所有 PR 的数据都给 PR 看（`api.py:65-79`），与「只看本人」不一致，一并确认（Q20） |

### 图 1 BI 看板

| 编号 | 图·节点 | 需求（摘） | 现状（证据） | 判断 | 建议改动 | 工作量 | 依赖 / 风险 |
|---|---|---|---|---|---|---|---|
| C-33 | 图1 三张汇总表 → BI看板前端；备注1 | 不读业务表，全部读已有中间汇总 | BI 全实时：商品表显式 `use_summary=False`（`bi_service.py:73-78`），其余 5 块直接查业务表（`advanced_repository.py:703-930`） | 缺失 | 补列后整页按一次 `freshness` 二选一（汇总 / 实时）；要补的列见 §2-C17；商品表直接用 `promo_cost`，`published_spend_by_goods` 可删 | M | 依赖迁移；等值测试 |
| C-34 | 图1「BI页面手动刷新按钮 重算当前筛选区间」 | — | BI 没有新鲜度组件（`BiDashboardPage.tsx:229-258`）；刷新成功后作废的查询不含 BI（`components/ReportFreshness/ReportFreshness.tsx:18-24`） | 缺失 | 切汇总后在 BI 标题栏放 `ReportFreshness`（preset / dateFrom / dateTo），`SUMMARY_BACKED_QUERY_KEYS` 加 `bi-dashboard`；后端零改动（复用 `POST /api/reports/summaries/refresh`） | S | 依赖 C-33 |
| C-35 | 图1 Tab1 店铺总览（KPI + 各类图表）/ Tab2 单品分析（选商品名称后渲染） | — | 单页：店铺 6 卡 + 推广 4 卡 + 1 张趋势折线 + 工作量表 + 单品表（`BiDashboardPage.tsx:227-378`）；后端还返回 ROI Top10 柱图、支付 Top10 饼图的数据，前端没画（`bi_service.py:103-137`）；无 Tab2 | 部分满足 | antd Tabs。Tab1=现有卡片 + 趋势 + ROI Top10 柱 + 支付占比饼 + PR 工作量（可点）；Tab2=商品下拉（选项取本区间 `style_performance`，只显示简称/全称）+ 单品 KPI + 日趋势（复用 `/api/reports/production/trend`）+ 笔记散点 | M | 图表全是手写 SVG（`package.json` 无图表库），柱/饼/散点要新写；Q18 |
| C-36 | 图1 Tab1 点击图表下钻 → 工作进度表 / 单品投产列表 | 跳转必须携带当前全部筛选参数 | 图表、表格都不可点；前端没有页面从 URL 恢复筛选（同 C-12）；工作进度只认月份 | 缺失 | 公共 hook：URL 参数优先于筛选记忆，且 URL 带来的值不写回记忆（不覆盖用户偏好）；BI → `/production?preset&date_from&date_to…`、`/work-progress?…&pr_id=` | M | 依赖 C-30（工作进度改成区间） |
| C-37 | 图1 Tab2 点击笔记散点 → 推广管理单据详情 | — | 无散点、无详情路由；PRD 原文没定义散点的坐标轴（§6 只有「点击笔记散点」一句）；生产 5156 单 like_count 全空（台账「已知数据缺口」） | 缺失 | 新端点按商品 + 区间返回笔记点（推广单级明细，读 promotion，属于「明细跳业务页面」）；默认 X=发布日期、Y=7 天点赞、点大小=站外推广成本；点击 → `/promotions?id=` | M | Q18；7 天数据回填前散点几乎为空 |
| C-38 | 图1 备注2 筛选条件记忆 | 复用 user_filter_pref | `bi_dashboard` 记 preset / granularity（`BiDashboardPage.tsx:100-125`），复用 user_preference 表（`user_preference_service.py:1-28`） | 已满足 | 拆 Tab 后把当前 Tab、Tab2 选的商品也记进去 | — | — |
| C-39 | 图1 备注3 仅展示，不可编辑 | — | BI 页没有编辑操作；后端的布局保存端点（`bi_api.py:45-66`）前端没有调用方 | 已满足 | — | — | — |
| C-40 | 图1 备注3 支持导出图片 | — | 没有；前端无截图类依赖（`frontend/package.json`） | 缺失 | 对当前 Tab 容器用 `html-to-image`（锁精确版本）导 PNG；导出时把批次 6 的水印画进图片 | S | Q19 |
| C-41 | 图1 备注4 / PRD §6 权限 | PR 只看本人工作量，屏蔽财务类投产成本指标；主管 / 老板 / 财务看全部 | `/api/reports/bi` 与布局端点都要 `report.production:read`（`bi_api.py:25-29,45-48`）→ PR、财务都 403；无字段裁剪 | 缺失 | 新 scope `report.bi:read_own` 授 pr，返回独立的「个人版」响应（白名单：本人工作量计数），财务类整块不返回；完整版仍用 `report.production:read`，给 finance 显式授；前端按响应形态渲染 | M | 财务类字段清单见 §2-C21 |
| C-42 | 图1 备注5 图表过滤 NULL | 防止渲染异常 | `MiniLineChart` 只收 `number[]`，无空值处理（`components/MiniLineChart/MiniLineChart.tsx:3-7,31-35,74-89`），调用方 `Number(x)`；现有曲线序列全部 COALESCE 成 0（`advanced_repository.py:851-855`、`production_service.py:187-192`），目前不会出 NULL；后端遗留图表把空 ROI 画成 0（`bi_service.py:122`） | 部分满足 | `MiniLineChart` 接受 `number \| null`，空值断线、不画点；新柱/饼/散点组件过滤 x 或 y 为空的点；遗留 charts 过滤 None ROI | S | Tab1 加 ROI/占比趋势、Tab2 散点（点赞多为空）时就会碰到 |
| C-43 | 图1 Tab1 KPI 卡片口径 | 与 PRD §9 店铺公式一致 | BI「花费占比」=站内÷(站内+站外)（`bi_service.py:165-166`），PRD 店铺「站内推广占比」=站内÷实际销售额；BI 店铺 ROI 不扣刷单（`advanced_repository.py:709-747`、`bi_service.py:167`），同页单品表却剔刷单（`bi_service.py:76-78`）；两张卡同叫「推广总花费」，一张是站内+站外、一张是已发布报价合计（`BiDashboardPage.tsx:289,312`）。BI 现口径来自旧版 `指标字典.md:110-113` | 冲突 | BI Tab1 直接复用店铺日/周/月同一套 PRD 公式（与 C-14 同一读取方法），卡片改名去重 | S | Q17 |

## 2. 逐题说明（brief 的 C1~C22）

主表已经给出每条的证据，这里只补需要论证的部分。

**C1 单品投产列表**（主表 C-01 / C-02 / C-03）

- 记忆字段：`preset / season / category / exclude_brushing`；自定义日期区间刻意不记（`ProductionPage.tsx:55-61`）。
  副作用：记住了 `preset=custom` 但没有区间，回来后页面空着等用户选日期（`components/ReportTimeRangeFilter/ReportTimeRangeFilter.tsx:15-20` 的 `enabled`），不报错。
- `stat_type: custom / week_7` **不需要落成列**。`product_roi_summary` 一行是（商品, 日）（`summary_models.py:60-98`），任何区间都是对天求和
  （`summary_read.py:167-213`），「近 7 天」只是另一段区间。存成 `stat_type` 行等于把同一份日数据换个形状再存一遍：
  多一个口径来源，还要单独定覆盖和刷新；`week_7` 的区间又依赖「今天」，按天固化就是 5b-1e 踩过的「依赖今天的派生值不能冻结」。
  所以它应是请求参数（或干脆两次调用），不是列。
- 现在每次都多算一份上期（环比）却没人看（C-03 证据）。加近 7 天后一次请求是 3 次聚合；汇总路径很快，实时全区间实测 215ms（台账 5a），
  可以接受。不用的话可以停算上期，但那是接口行为变化，本次不建议动。
- 近 7 天以哪天为终点 → Q1。

**C2 店铺日 / 周 / 月**（C-11 ~ C-14）

- `StoreDailyPage.tsx` 是「报表与分析 → 店铺数据」，一页 + 粒度下拉，周/月在前端相加；`DailyDataPage.tsx` 是「数据管理」下千牛 / 万相台
  原始导入明细的通用表格（带导入按钮、服务端筛选排序），不是店铺报表。
- `shop_week_summary` / `shop_month_summary` 只写不读（C-13）。
- 现有列：日期、访客数、支付金额、支付件数（标题误写「支付订单数」）、全站推 / 直通车 / 引力魔方消耗（手填层，没有录入入口）+ 千牛 extra 的数值列。
- 周/月比率是否「总和重算」：typed 指标都是可加的，前端相加没问题；**extra 不行**。从仓库根 `千牛输入导入模版.xlsx` 第 5-6 行看，
  真实导出的「商品详情页跳出率」「下单转化率」是 `78.15%` 这样的字符串，代码去掉 `%` 后直接相加（`store_daily_service.py:102`）。
  千牛 38 列里不可加的至少有：平均停留时长、商品详情页跳出率、下单转化率、商品支付转化率、访客平均价值、竞争力评分、搜索引导支付转化率、
  结构化详情引导转化率、结构化详情引导成交占比，以及年/月累计支付金额、月累计支付件数（同一天跨商品可加、跨天不可加）。
  万相台的 点击率、平均点击花费、千次展现花费、点击转化率、投入产出比、含预售投产比、总成交成本、加购率、各类收藏/加购成本、宝贝收藏率、
  引导访问潜客占比、入会率、引导访问率、平均访问页面数、成交新客占比、人均成交笔数/金额 同理（投产按商品跨天相加）。

**C3 站外加购**（C-06 / C-07）

- PRD 原文（docx §9「单品投产相关」）：`站外来的加购总数 = 商品全渠道总加购件数 − 站内加购数量`，`< 0 则取 0`（估算口径，混入自然流量）；
  `总的加购数量 = 商品全渠道总加购件数`；`站内加购成本 = 站内投放金额 ÷ 站内加购数量`；`总加购成本 = (站内投放金额 + 站外推广成本) ÷ 总的加购数量`。
  推测正确：「负数置 0」指站外加购这一条。
- 现在：总加购数 = 千牛「商品加购件数」typed 列之和；加购成本 = 推广总花费 ÷ 总加购数，即 PRD 的「总加购成本」。站内加购、站外加购、站内加购成本都没有。
- 万相台真实导出的加购相关键（仓库根 `站内商品推广数据 表格导入.xlsx` 表头，`final.xlsx`「单品站内推广数据」同名，后者就是生产 2026-03-23 那 38 行）：
  `总购物车数`、`直接购物车数`、`间接购物车数`、`加购率`、`加购成本`、`总收藏加购数`、`总收藏加购成本`、`宝贝收藏加购数`、`宝贝收藏加购成本`。
  「站内加购数量」推荐取 `总购物车数`（= 直接 + 间接）。它们只在 `ad_daily.extra` 里；adapter 默认列和单测样例都不含（C-06 证据）。
  按台账的教训，落 typed 列时要从真实导出行（千分位、`-` 占位）走 adapter 写测试。

**C4 商品两张表改成本价**（C-08 / C-09）

- 投产没有任何指标用到货品成本；PRD 唯一用到成本价的是毛利（`销售货品总成本 = 区间支付件数 × 单品货品成本价`）。
- 改成本后**不该变**：已有推广单的样品成本 / 站外推广成本（建单快照 + PR 微调 + 金额时间线，PRD 模块二要求修改留痕）。
  **该变**：新建送拍/置换单的默认样品成本（已经是，`promotion/repository.py:322-341`）；毛利类指标（做了之后）。
- 改成本现在不会触发任何汇总刷新。推荐毛利在读取时用当前成本价现算，汇总表只多存 `pay_orders`，这样改价立即生效、不需要刷新、
  实时与汇总两条路径天然相等；代价是历史毛利随改价一起变（Q4）。

**C5 仅退款率**（C-15）：手填字段、PUT 接口、清空问题、展示面都见主表。按 10-02 的口径「按天存金额不存比率」，周/月/BI 用 `Σ仅退款÷Σ支付` 重算。

**C6 Excel 兜底与 data_source**（C-16 / C-17）：见主表。补一点：仓库里只有商品级的千牛模板（`千牛输入导入模版.xlsx`，第 5 行表头），
没有店铺级导出样本，所以「店铺日数据 Excel 导入」要先确认业务能拿到什么导出。

**C7 刷新口径**（C-18 ~ C-21）

1. 每小时只刷最近 31 天：满足。
2. 手动刷新可重算任意历史区间：端点满足；按钮只在汇总态出现是 5b-1c 刻意的设计。实时态的数字就是此刻按明细现算的，「重算」没有对象；
   这时刷新反而会把这段历史改成读汇总表、随历史冻结（与 `tasks/summary_tasks.py:223-227` 不给未覆盖日子刷新是同一理由）。判等效满足、建议不改；最小改法见 C-20。
3. 先删后插、禁止 truncate：满足。054 / 055 清的是覆盖记录（`054_summary_read_columns.py:92`、`055_qianniu_refund_cart.py:64`），不是汇总表。
4. 店铺周/月每日凌晨 T+1：随每小时任务刷，比 PRD 更频繁，满足。
5. BI 页没有刷新入口（C-34）。

**C8 商品维度**（C-23）

- 现状：唯一键 `(tenant_id, pr_id, stat_date) NULLS NOT DISTINCT`，逐日调 `aggregate_by_pr` 后区间删 + 批量插。
- 推荐**改唯一键（加维度）而不是新表**：
  - 新表要么第二份聚合 SQL（违反「刷新只复用一条 SQL」），要么照样得泛化 `aggregate_by_pr`；
  - PR 表也要支持季节筛选（图 2 的筛选作用于整页），没有商品维度就只能 PR 表走实时、卡片走汇总，同页两种新鲜度。
- 影响：刷新仍是 31 次查询（单次实测 11ms），只多一个 GROUP BY 维度和「主商品推定」的 LATERAL；行数从「每天 PR 数」变成「每天 PR×商品组合数」（SQL-3 可量）。
  `NULLS NOT DISTINCT` 对 `pr_id`、`goods_main_id` 两个可空列同时生效，但必须原生 DDL 重建、ORM 不声明（台账「踩过的坑」）。
  必须按 054 规则清覆盖。旧行 `goods_main_id` 为空，不删（遵守「禁止 truncate」）：覆盖清掉后读不到，之后任何区间刷新都会先删后插替换掉。

**C9 单品汇总卡片**（C-26 / C-27）：图上的「季节多选 + 筛选记忆」与 10-03「手选一个季节并锁住（存筛选记忆）」只是多选/单选之差，
意图一致——推荐合一（Q10）。推广单归属商品走 `promotion.goods_main_id`，为空回落主商品，与投产 promo_cost 同口径，卡片上的推广成本才能和投产报表对上。
发文进度表（`/publish-progress`）已经有「款式卡片 12 个 / 按 PR / 按半月」这套交互（`repository.py:91-221`），
旧 Excel（`final.xlsx`「BI_发文进度表」）也是这个结构，工作进度新卡片可以照它做；上线后发文进度表是否下线请一并确认。

**C10 PR 明细弹窗**（C-28）：列表接口能按 `pr_id` + 合作日期筛；缺「未分配」、商品/季节筛选，以及能直接打开的推广单详情（路由或抽屉）。

**C11 周明细**（C-29）：周按 ISO（周一开始），实时 SQL、汇总刷新、前端、导出四处已一致（`advanced_repository.py:68-84`、
`summary_refresh_service.py:142-152`、`StoreDailyPage.tsx:28-33`、`export_service.py:98-105`）。分周的日期轴见 Q13：
旧 Excel 的周期表按「预定发布日期」分旬/半月（`final.xlsx`「BI看板」第 14-17 行表头是「档期内应发布」；「BI_发文进度表」第 35-42 行公式按「站外推广表」N 列=预定发布日期过滤）。

**C12 导出**（C-30）：20 列 = 页面 20 列（`export_service.py:27-48`），筛选只有月份，sheet 名 `work-progress`，文件名 `work-progress_<起>_<止>.xlsx`（`export_service.py:144,152`）。

**C13 约稿量来源**（C-22）：只来自推广单；分支 A 若改成「已定稿才生成推广单」，谈款中、待老板审核的都不再计入，合作日期也会后移到定稿当天 → Q14。

**C14 笔记访客 / 加购**（C-31）：推广单上没有这两个字段（typed 和 `source_extra` 约定里都没有）；推广单导入不能导。PRD 工作进度公式里用到的是
「加购成本（人数）= 约稿总金额 ÷ 总加购人数」「加购成本（件数）= 约稿总金额 ÷ 总加购件数」，没有用访客的公式；现在的工作进度没有任何加购/访客指标。

**C15 分母为 0**（C-25）：工作进度 6 个比率（信息完整率、召回完成率、超时率、月度完成率、爆文率、CPL）都走 `safe_div`。旧 Excel 的同一张表里能看到 `#DIV/0!`（`final.xlsx`「BI看板」第 5 行召回完成率），现系统返回空。

**C16 PR 只看本人**（C-32）：台账描述核对属实。补充：财务也没有报表权限；发文进度表给 PR 看全员数据。方案见主表。

**C17 BI 切汇总表要补的列**（C-33）

| BI 块 | 现在的实时来源 | 汇总表现状 | 要补 |
|---|---|---|---|
| 店铺总览：销售额 | 全部千牛日报 `pay_amount` | `shop_daily_summary.pay_amount` ✓（全店口径，含未归属日报） | — |
| 店铺总览：退款 | 全部千牛日报 `refund_amount` | 无 | `shop_daily_summary.refund_amount` |
| 店铺总览：站内花费 | 全部 `ad_daily.cost` | `product_roi_summary.ad_spend` 只含已映射商品 | `shop_daily_summary.ad_spend` |
| 店铺总览：站外花费 | 全部已发布推广单 `total_promo_cost` | `product_roi_summary.promo_cost` 不含无商品归属的单（`advanced_repository.py:546`） | `shop_daily_summary.promo_cost` |
| 店铺 PRD 公式（C-14 / C-43） | — | — | `shop_daily_summary.brushing_amount`（全店刷单本金） |
| 经营趋势 | 同上四项按桶 | 补完上面四列即可按 `bucket_expr` 读 | — |
| 推广费用 4 卡（按发布状态的报价合计 + 篇数，`advanced_repository.py:749-787`） | 推广单 | `pr_work_progress_summary` 有 quote / publish / cancel 计数，无金额、无「未发布」计数 | `quote_amount`、`published_quote_amount`、`unpublished_quote_amount`、`cancelled_quote_amount`、`pending_count` |
| 员工工作量（`advanced_repository.py:789-836`） | 推广单 + target_planning | 缺 `pending_count`；目标篇数读配置表即可 | 同上 |
| 单品表现 | `ProductionService.get_report(use_summary=False)` | `product_roi_summary` ✓；BI 的单品站外花费与 `promo_cost` 逻辑相同（`advanced_repository.py:378-398` 对 `897-930`） | — |

三张店铺汇总表同步补列（周/月要能独立出店铺报表）。

**C18 BI 手动刷新**（C-34）：见主表，前提是 C-33。

**C19 Tab 与下钻**（C-35 ~ C-37）

- 两个目标页都不能从 URL 恢复筛选：前端没有任何页面读 URL 参数；工作进度只有月份。
- PRD BI 章节原文（docx §6）只写了「Tab1 店铺总览看板 KPI卡片+各类图表」「Tab2 单品分析看板（选择商品后渲染）」「点击笔记散点 → 推广管理单据详情」，
  **没有给 Tab2 的图表清单，也没有定义散点的坐标轴** → Q18。
- 现在下钻到 `/promotions` 也没有落点（无详情视图）。

**C20 导出图片与「不可编辑」**（C-39 / C-40）：BI 没有可编辑的数据。批次 6 的全页水印如果是挂在应用根部的固定定位层，截取 Tab 容器时不会带上，
需要导出时把水印画进图片（推荐带，Q19）。图表都是 SVG + HTML 卡片，`html-to-image` 两者都能截。

**C21 PR 权限隔离**（C-41）：财务类投产成本指标 = 店铺总览全部（销售、退款、退货率、站内/站外/总花费、两个占比、ROI）、
推广费用 4 卡的金额、单品表现全部列、经营趋势全部序列、遗留 `cards` 里的支付额与 `charts` 全部。非财务 = 员工工作量的计数列和推广篇数。
推荐后端返回独立的个人版响应（白名单），比在完整响应上逐字段置空安全：以后往完整版加字段不会自动漏给 PR。

**C22 图表 NULL**（C-42）：会出现 NULL 的地方——单品/店铺 ROI（花费为 0）、退货率与各类占比（支付为 0）、7 天点赞（未录入）、单篇点赞成本、仅退款率（未录入）。
现在的两张折线图（投产趋势 6 条序列、BI 趋势 4 条）都不含这些，所以还没出过问题。

## 3. 需业务确认的问题（附推荐默认答案）

| # | 问题 | 推荐默认答案 | 理由 |
|---|---|---|---|
| Q1 | 单品投产「近 7 天」以哪天为终点 | 昨天往前 7 天（T-7 ~ T-1），固定，不随筛选变 | 千牛数据 T+1 导入，含今天会变成 6 天销售配 7 天成本；跟随筛选末日的话，选「近 7 天」时两套数据完全一样。备选：最近一个有千牛日报的日子 |
| Q2 | 商品编码里带千牛 ID（`SUIT-<千牛ID>`、`<货号>-<千牛ID>`）怎么处理 | 报表页不显示商品编码（用简称 + 含款号），商品页提示改掉，存量编码不改。若业务要求所有页面都不出现千牛 ID，再做一次性改码（套装 `SUIT-<首个成员货号>`，重名加 `-2`；单件多链接 `<货号>-2`） | PRD 全局约束 1 要求隐藏千牛 ID；但商品编码按设计建档后不可改，历史审计与企微消息里用的是旧编码，改码代价更大 |
| Q3 | 推广单件成交成本按 PRD 改为「总推广成本 ÷ 支付件数」 | 改 | 现实现与 PRD 不同且恒为空 |
| Q4 | 改货品成本价后，历史毛利要不要跟着变 | 跟着变（读取时用当前成本价） | 系统没有成本价历史；推广单的样品成本是快照，不受影响 |
| Q5 | 店铺报表「站内推广费总额」取哪个 | 默认万相台导入合计（与投产、BI 同源）；某天手填了全站推/直通车/引力魔方就用手填合计 | 手填层本就是 override 语义，且生产 0 行 |
| Q6 | PRD 店铺公式疑似笔误：「推广总占比」与「净推广占比」公式相同；「投产（扣除刷单）」里没扣刷单 | 推广总占比 = 推广总额 ÷ 支付总额；净推广占比 = 推广总额 ÷ 实际销售额；投产（扣除刷单）=（支付总额 − 刷单本金）÷ 推广总额 | 让名字和公式对得上 |
| Q7 | 仅退款率遇到没录的日子 | 分子分母都只算已录入的日子，旁边显示「已录 x/y 天」 | 没录当 0 会把比率压低 |
| Q8 | 谁录仅退款金额 | 运营 + 管理员（给 operations 授 `report.store_daily:write`） | 运营本来就看店铺数据 |
| Q9 | 店铺访客数口径 | 一期改名「商品访客数合计」；要真实店铺访客，需要业务提供生意参谋「店铺整体」日报导出，再加导入通道 | 现在是商品访客数相加，会重复计 |
| Q10 | 工作进度的季节 | 一个季节多选，存进筛选记忆即「锁定」，卡片 / 表 / 导出共用；没选季节时卡片取全部商品前 12 | 单选是多选的特例；生产只有 2 个商品有季节，默认按季节会空 |
| Q11 | 单品卡片「推广成本」口径 | 与该卡约稿量同一批单据（全部有效推广单）的站外推广成本（服务费 + 样品 + 运费） | 卡片看的是投入；投产报表仍只计已发布 |
| Q12 | 工作进度「成本(含衣服)」与 CPL | 改用站外推广成本，CPL = 站外推广成本 ÷ 点赞 | 现在只含样品成本，名不副实；与 PR #27 单篇点赞成本口径一致 |
| Q13 | 周期明细按合作日期还是预定发布日期分周 | 合作日期 | 与 PR 表、汇总表同一时间轴，周行之和等于 PR 表合计。旧 Excel 按预定发布日期分旬，若业务更习惯，作为第二视图（实时查询） |
| Q14 | 分支 A 改成「已定稿才生成推广单」后，约稿量算不算谈款中的单 | 不算，约稿量 = 推广单数；另加「谈款中」计数列 | 谈款中的单还没有合作日期 |
| Q15 | 工作进度「加购成本（人数 / 件数）」 | 一期不做 | PRD 公式要笔记级加购，系统没有；千牛加购只能到商品，分不到 PR |
| Q16 | 笔记效果 Excel 兜底与「7 天数据截图必传」（4b-1 已定）冲突 | 一期不做；以后的导入 / API 只写访客、加购，点赞 / 收藏 / 评论仍走截图录入 | 守住已定口径 |
| Q17 | BI 店铺卡片口径 | 对齐 PRD 店铺公式（占比按实际销售额、扣刷单），两张「推广总花费」卡改名 | 同一个「站内推广占比」在两个页面算法不同会被问 |
| Q18 | Tab2 单品分析内容与散点坐标 | KPI（支付、退款、净投产、站内/站外花费）+ 日趋势 + 笔记散点（X=发布日期，与趋势图对齐；Y=7 天点赞；点大小=站外推广成本），点击跳推广单详情 | 能看出哪篇笔记发布后销售有起伏 |
| Q19 | 导出图片带不带水印 | 带，内容同批次 6 | 改动 6 的目的就是截图外泄可追责 |
| Q20 | PR 在工作进度 / BI 能看到什么 | 工作进度：本人全部列（含金额，PR 本来就能看自己单据的报价和样品成本，`backend/app/core/security/field_permissions.py:57-66`）；BI：只看本人工作量计数。发文进度表现在给 PR 看全员数据，是否同步收紧也请确认 | PRD §5、§6 |
| Q21 | 汇总刷新按钮给谁 | 给 pr_manager、operations 显式授 `report.summary:refresh` | 现在只有 admin；action 名不会被通配捞走，单独授安全 |

## 4. 迁移与存量数据影响

### 4.1 建议合成一个 migration（编号由编排方统一分配）

每个改汇总表列或口径的 migration 都要清一次覆盖记录（054 定的规则），清完所有报表回退实时、要等每小时任务重写最近 31 天。
所以建议下面这些**一次做完**，并和批次 6 一起决定要不要同时给 `product_roi_summary` / 店铺汇总加 `channel` 维度（直播拆两行、大盘只计普通渠道），
否则批次 6 还要再改一次唯一键、再清一次覆盖。

| 表 | 变更 | 服务的条目 |
|---|---|---|
| `ad_daily` | 加 `add_cart_count int`（万相台「总购物车数」）；从 `extra` 回填存量（宽松解析同 055：去千分位，`-` / 空 / 认不出记 NULL） | C-06 |
| `product_roi_summary` | 加 `pay_orders bigint`、`ad_add_cart_count bigint`（NOT NULL DEFAULT 0） | C-05 / C-06 / C-08 |
| `shop_daily_summary`、`shop_week_summary`、`shop_month_summary` | 加 `refund_amount numeric`、`brushing_amount`、`ad_spend`、`promo_cost`（numeric(16,2)，NOT NULL DEFAULT 0） | C-14 / C-33 / C-43 |
| `pr_work_progress_summary` | 加 `goods_main_id uuid NULL`（FK goods_main）；DROP 旧唯一索引，原生 DDL 建 `(tenant_id, pr_id, goods_main_id, stat_date) NULLS NOT DISTINCT`；加 `pending_count int`、`quote_amount`、`published_quote_amount`、`unpublished_quote_amount`、`cancelled_quote_amount`、`recall_quote_amount`、`promo_cost`（numeric(16,2)） | C-23 / C-24 / C-26 / C-33 |
| `store_daily` | 加 `refund_only_amount numeric(12,2) NULL`（可选同时加 `data_source`） | C-15 / C-17 |
| `report_summary_coverage` | `DELETE FROM report_summary_coverage`（054 规则） | 全部 |
| 权限 seed（照 052 的写法） | `report.work_progress:read_own`→pr；`report.bi:read_own`→pr；finance 授 `report.work_progress:read`、`report.production:read`（导出权限随批次 6 的新 action）；按 Q8 / Q21 授 operations `report.store_daily:write`、pr_manager / operations `report.summary:refresh` | C-32 / C-41 / C-15 / C-20 |

同一个 PR 里要改的刷新源（仍然「零新增聚合 SQL」）：`StoreDailyRepository.aggregate` 补 4 列；`WorkProgressRepository.aggregate_by_pr`
补金额列 + 按商品分组；`ProductionRepository.daily_trend_by_goods` 补 `pay_orders` 与站内加购。每条都要补等值测试
（JSON 比对、拦截 `record_source`、场景有效性断言）并做故障注入。

商品编码改码（仅当 Q2 选择改码）是单独的数据迁移：不涉及汇总表（编码和名称都是读取时 JOIN），不需要清覆盖；
会改变导出的「商品编码」列、搜索结果和以后的企微消息，要通知业务。默认方案（报表页不显示编码）不需要迁移。

### 4.2 对已有数据的影响

- 清覆盖后，所有报表回退实时，直到下一个 :20 的每小时任务重写最近 31 天；31 天外的历史一直走实时（数字正确，投产全区间实测 215ms），
  需要的话管理员用手动刷新补（每次 ≤366 天）。
- `pr_work_progress_summary` 旧行的 `goods_main_id` 为空。不删（遵守「禁止 truncate」）：覆盖已清，读取侧不会用它；之后任何区间刷新先删后插即被替换。
- 新增的汇总列默认 0，覆盖已清，不会被读成 0。
- `ad_daily` 回填只影响生产那 38 行（台账：只有 2026-03-23 一天）；`qianniu_daily` 不动。
- `store_daily` 生产 0 行（台账 5a），加列无影响。
- 权限缓存有 TTL（`backend/app/core/security/permissions.py:61-82`），seed 后用户最晚在 TTL 后拿到新权限。

### 4.3 需要生产核对的只读 SQL

以下全部是 SELECT，按租户执行（这些表都开了 RLS）。

```sql
-- SQL-1 商品编码里带千牛 ID 的商品数（C-01 / Q2）
SELECT count(*) FILTER (WHERE goods_code ~ '^SUIT-[0-9]{9,}$')                       AS suit_code_has_qn_id,
       count(*) FILTER (WHERE goods_code ~ '-[0-9]{9,}$' AND goods_code !~ '^SUIT-') AS link_code_has_qn_id,
       count(*)                                                                       AS goods_total
FROM goods_main WHERE is_deleted = false;

-- SQL-2 季节填写情况与字典（C-26 / Q10）
SELECT COALESCE(season, '(空)') AS season, count(*) AS goods
FROM goods_main WHERE is_deleted = false GROUP BY 1 ORDER BY 2 DESC;
SELECT value, sort_order, is_active FROM dict_item WHERE dict_type = 'season' ORDER BY sort_order, value;

-- SQL-3 推广单的商品归属覆盖率；加商品维度后每天的行数（C-23）
SELECT count(*) AS active_promotions,
       count(goods_main_id) AS with_goods_main_id,
       count(*) FILTER (WHERE publish_status = '已发布') AS published,
       count(*) FILTER (WHERE metrics_recorded_at IS NOT NULL) AS metrics_recorded
FROM promotion WHERE is_active = true;
SELECT cooperation_date,
       count(DISTINCT COALESCE(pr_id::text, '-') || ':' || COALESCE(goods_main_id::text, '-')) AS pr_goods_pairs,
       count(DISTINCT COALESCE(pr_id::text, '-')) AS prs
FROM promotion WHERE is_active = true
GROUP BY cooperation_date ORDER BY pr_goods_pairs DESC LIMIT 10;

-- SQL-4 万相台加购相关键、站外加购为负的比例（C-06）
SELECT k, count(*) AS rows
FROM ad_daily a CROSS JOIN LATERAL jsonb_object_keys(a.extra) AS k
WHERE k LIKE '%购物车%' OR k LIKE '%加购%'
GROUP BY k ORDER BY k;
WITH ad AS (
  SELECT pp.goods_main_id, a.date,
         sum(CASE WHEN replace(a.extra->>'总购物车数', ',', '') ~ '^[0-9]+(\.[0-9]+)?$'
                  THEN replace(a.extra->>'总购物车数', ',', '')::numeric END) AS ad_cart
  FROM ad_daily a JOIN platform_product pp ON pp.id = a.platform_product_id
  WHERE pp.goods_main_id IS NOT NULL
  GROUP BY 1, 2
), qn AS (
  SELECT pp.goods_main_id, q.date, sum(q.add_cart_count) AS all_cart
  FROM qianniu_daily q JOIN platform_product pp ON pp.id = q.platform_product_id
  WHERE pp.goods_main_id IS NOT NULL
  GROUP BY 1, 2
)
SELECT count(*) AS goods_days,
       count(*) FILTER (WHERE COALESCE(qn.all_cart, 0) - ad.ad_cart < 0) AS offsite_negative,
       sum(ad.ad_cart) AS ad_cart_total,
       sum(qn.all_cart) AS all_cart_total
FROM ad LEFT JOIN qn USING (goods_main_id, date);

-- SQL-5 报表 / 商品相关权限的实际授予（C-02 / C-32 / C-41）
SELECT r.code AS role, p.scope
FROM role r
JOIN role_permission rp ON rp.role_id = r.id
JOIN permission p ON p.id = rp.permission_id
WHERE p.scope = '*' OR p.scope LIKE 'report%' OR p.scope LIKE 'product%'
ORDER BY 1, 2;
SELECT u.username, p.scope, o.effect
FROM user_permission_override o
JOIN permission p ON p.id = o.permission_id
JOIN "user" u ON u.id = o.user_id
WHERE p.scope LIKE 'report%' OR p.scope LIKE 'product%'
ORDER BY 1, 2;
SELECT r.code AS role, count(*) AS users
FROM user_role ur JOIN role r ON r.id = ur.role_id GROUP BY 1 ORDER BY 1;

-- SQL-6 店铺数据页上「商品详情页跳出率」实际显示的是什么（C-11）
SELECT q.date, count(*) AS item_rows,
       sum(CASE WHEN replace(replace(q.extra->>'商品详情页跳出率', '%', ''), ',', '') ~ '^[0-9]+(\.[0-9]+)?$'
                THEN replace(replace(q.extra->>'商品详情页跳出率', '%', ''), ',', '')::numeric END)
         AS bounce_rate_as_shown
FROM qianniu_daily q
GROUP BY q.date ORDER BY q.date DESC LIMIT 5;

-- SQL-7 覆盖现状与手填层（§4.2）
SELECT min(stat_date) AS covered_from, max(stat_date) AS covered_to,
       count(*) AS covered_days, min(refreshed_at) AS oldest_refresh
FROM report_summary_coverage;
SELECT count(*) AS store_daily_rows FROM store_daily;

-- SQL-8 合作日期与预定发布日期跨月的比例（Q13）
SELECT count(*) FILTER (WHERE scheduled_publish_date IS NOT NULL) AS with_schedule,
       count(*) FILTER (WHERE date_trunc('month', scheduled_publish_date)
                              <> date_trunc('month', cooperation_date)) AS schedule_in_other_month
FROM promotion WHERE is_active = true;
```

## 5. 建议的实施顺序

按依赖排；人日按推荐默认答案估，含后端测试与故障注入。

1. **业务确认**：Q1~Q21，其中 Q5 / Q6 / Q10 / Q13 / Q18 决定汇总列和页面形态，必须在迁移前定；同时和批次 6 对齐 channel 维度。
2. **公共底座（约 2 人日）**：推广单只读详情（`/promotions?id=`）；推广列表接口加商品 / 季节 / 未分配筛选；报表页「URL 参数优先于筛选记忆」的公共 hook；
   报表侧的季节 / 类目选项接口（解决 pr / pr_manager 没有 `product:read`）。服务 C-02 / C-28 / C-29 / C-36 / C-37。
3. **汇总层一次迁移（约 3 人日）**：§4.1 全部 + 刷新源改造 + 等值测试。之后 4 / 5 / 6 互不依赖。
4. **投产单品页（约 3 人日）**：C-01 / C-03 / C-05 / C-06 / C-08 / C-11。C-11（extra 不可加列）不依赖迁移，可以最先单独修。
5. **店铺日 / 周 / 月 + 仅退款（约 4 人日）**：C-12 ~ C-17；菜单改成「投产分析」四个子菜单。
6. **工作进度（约 4 人日）**：C-22 ~ C-32，含 `read_own` 权限与 finance 授权；时间筛选改成区间（为 BI 下钻做准备）。
7. **BI（约 5 人日）**：C-33 切汇总 → C-34 刷新按钮 → C-35 拆 Tab → C-36 下钻 → C-37 Tab2 散点 → C-41 个人版 → C-40 导出图片 → C-42 / C-43。
   依赖 2 / 3 / 5 / 6。
8. **批次 6 接入**：水印画进导出图片；导出权限改独立 action；直播分账若没在第 3 步一起设计，这里要再迁移一次并清覆盖。

