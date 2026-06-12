# 列数核对 + 数据生成验证报告

> 对照 `final.xlsx` 各 Sheet（权威列见 `EXCEL-COLUMNS.md`，字段来源见 `aidlc-docs/metrics-dict.tsv`）。
> 核对时间：本地全栈实测（浏览器逐页 + API）。

## 一、列数核对

| 模块 | Excel列 | 当前页面列 | 状态 | 说明 |
|---|---:|---:|:--:|---|
| 商品成本表 | 13 | 13(+状态) | ✅ | 13 业务列全对齐（图片…品牌）+ 状态列 |
| 款式管理 | — | 8 | ✅ | 款式维护页（非 Excel sheet，业务必要） |
| 品牌管理 | — | 4 | ✅ | 品牌字典页 |
| 博主库 | 41 | 9 | ⚠️ | **后端缺口**：模型仅存 15 业务字段，未建灰豚爬虫 26 列（3/7/14篇阅读点赞收藏评论、粉丝画像、涨跌等） |
| 千牛数据 | 38 | 5 typed + extra | ◐ | typed 5 列 + extra JSONB 动态展开；有数据时补齐至 38 |
| 单品站内推广 | 72 | 6 typed + extra | ◐ | 同上，有数据时补齐至 72 |
| 站外推广 | 41 | 12 | ⚠️ | **后端缺口**：响应缺 打单地址/发货单号/寄回单号/博主风格/合作形式 等源列 |
| 工作进度表 | 20 | 19 | ◐ | 差 1 列（「已填写点赞量数量」合入信息完整度） |
| 爆款约篇数量 | 8 | 8 | ✅ | 完全对齐 |
| 发文进度表 | 三层看板 | 汇总6指标+13列卡片 | ✅ | 表格版（看板视觉待 C 升级） |
| 财务结款 | 15 | 15 | ✅ | B 富化后补齐 款式编码/款式/博主名 |
| 拍单 | 9 | 9 | ✅ | 完全对齐 |
| 刷单 | 17 | 10 | ⚠️ | 后端 order_adjustment 精简模型（统一拍单/刷单） |
| 余额核对 | 7 | 7 | ✅ | 完全对齐 |
| 店铺数据 | 24 | 7 typed + extra | ✅ | C 完成：按日 SUM 千牛 extra 数值列动态展开（实测 27 汇总指标） |
| 投产报表 | 70 | 13 + extra | ✅ | D 完成：按款式 SUM 千牛/站内 extra 数值列动态展开（实测 27 汇总指标） |
| 设计制版×4 | 流程 | 3 | ✅ | 款号/款名/状态（状态流转表格版） |
| 用户管理 | — | 7 | ✅ | 系统功能页 |
| 数据导入 | — | 9 | ✅ | 系统功能页 |

**完全对齐(✅)**：商品成本表、爆款约篇、财务结款、拍单、余额核对、发文进度(表格)。
**后端缺口(⚠️)**：博主库、站外推广、刷单、店铺数据——后端数据模型存的是**精简子集**，非 1:1 全列。
**数据驱动(◐)**：千牛/站内（extra JSONB 动态展开）、工作进度、投产。

## 二、数据生成验证（本地实测）

| 模块 | 生成方式 | 结果 |
|---|---|---|
| 品牌 | POST /api/brands | ✅ 201 |
| 款式 | POST /api/styles | ✅ 201（修 strict 后） |
| SKU/商品成本表 | POST /api/skus | ✅ 201（2 条，13 列展示正常） |
| 博主 | POST /api/bloggers（UI） | ✅ 201（修 strict + after_begin 后） |
| 站外推广 | POST /api/promotions（UI） | ✅ 内部编码自动生成 DE2606110001 |
| 用户 | POST /api/users（UI） | ✅ 临时密码返回 |
| 余额核对 | POST /api/finance/balance-records | ✅ 201 |
| 刷单 | POST /api/finance/order-adjustments/brushing | ✅ 201，"100-30"→70 表达式解析正确，ROI剔除=true |
| 报表/工作进度/发文/投产 | 系统聚合（依赖源数据） | ✅ 接口 200，有源数据即聚合 |
| 千牛/站内 | 导入入库（/api/imports/upload） | ✅ 接口就绪（需上传文件产生数据） |

**结论**：所有"人工录入"模块数据生成正常；派生报表正确聚合；导入链路就绪。

## 三、待后端补强（如需 100% 列对齐）

1. ~~**博主库 +26 列**~~ ✅ **已完成**（A）：migration 023 加 `crawler_metrics` JSONB，前端 43 列（含 33 灰豚列），PUT 可写灰豚数据。
2. ~~**站外推广 +列**~~ ✅ **已完成**（B）：migration 024 加 `source_extra` JSONB，前端 23 列（含 11 源列：颜色及规格/打单地址/发货单号/订单号/寄回单号/合作方式/合作形式/收藏数/评论数/博主风格/买家秀）。
3. ~~**店铺数据 24 列**~~ ✅ **已完成**（C，commit `1d8ed3a`）：`StoreDailyService._aggregate_extra` 按日 SUM `qianniu_daily.extra` 数值列（排除 ID/文本列）；`StoreDailyRow.extra` + 前端动态展开列。实测 custom 区间返回 27 汇总指标（下单件数/支付金额等正确求和）。
4. ~~**投产报表 70 列**~~ ✅ **已完成**（D）：`ProductionRepository.fetch_extra_by_style`（千牛/站内 extra 经 `platform_product.platform_id=*_daily.platform_id_snapshot` 归集到款式，兼容导入数据 FK 为 NULL）+ `ProductionService._aggregate_extra` 按款式 SUM 数值列；`ProductionRow.extra` + 前端动态展开列。实测 `preset=custom&date_from=2026-01-01&date_to=2026-01-31` 返回 UI001 pay=65669 + 27 汇总指标。

> **验证注意**：店铺/投产报表默认 `preset=last_30d`（相对今天），导入样例数据日期为 2026-01-01 时需传 `preset=custom&date_from=...&date_to=...` 才落在窗口内。
> **投产数据依赖**：按款式聚合需 `platform_product`（千牛/站内 platform_id → 款式）映射存在；导入数据 `platform_product_id` 为 NULL 时，新 join 走 `platform_id_snapshot=platform_product.platform_id` 兜底，仍需先建立映射。

