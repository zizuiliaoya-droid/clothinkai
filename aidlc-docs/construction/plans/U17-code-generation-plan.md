# U17 代码生成计划（Code Generation Plan）

> 单元：U17 — 套装 + BI 看板 + 报表导出（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 分批：**2 批** + Build & Test
> Build & Test：Docker PG16:5560 + Redis7:6415 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 套装落 modules/product（bundle_models/schemas/repository/service/api）；BI/导出落 modules/report（user_preference_models/service + bi_service + export_service + bi_api + export_api）。
- [Answer] migration 021：bundle_product + bundle_item + user_preference 3 表 + product.bundle/report.export scope seed。
- [Answer] EP02-S08 BundleService split_quantities；EP09-S06 BiService 复用 report service + 布局 user_preference；EP09-S08 ReportExportService openpyxl 流式 + report.export:read 403。

---

## 1. 步骤（2 批）

### Batch 1 — 模型 + Schema + 权限 + 异常 + 指标 + repository
- [x] 1.1 product/bundle_models.py（BundleProduct + BundleItem）+ report/user_preference_models.py
- [x] 1.2 product/bundle_schemas.py（4 schema）
- [x] 1.3 product/permissions +bundle scope / report/advanced_permissions +export / report/exceptions +ReportExportTypeInvalidError
- [x] 1.4 core/metrics +report_export_total
- [x] 1.5 product/bundle_repository.py（BundleRepository）

### Batch 2 — Service + API + Deps + main + migration + conftest + 测试
- [x] 2.1 product/bundle_service.py（create + get_with_items + split_quantities）+ bundle_api.py
- [x] 2.2 report/user_preference_service.py + bi_service.py（get_dashboard + DEFAULT_BI_LAYOUT）+ export_service.py（openpyxl 流式）
- [x] 2.3 report/bi_api.py（bi + layout）+ export_api.py（{type}/export）
- [x] 2.4 product/deps +BundleServiceDep / report/deps +Bi/Export/UserPreference ServiceDep
- [x] 2.5 main.py 挂 3 router + migration 021 + conftest import
- [x] 2.6 测试 3 文件（unit/integration/api）

### Build & Test
- [x] B.1 Docker PG16:5560 + Redis7:6415；alembic upgrade head（含 021）；U17 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部 2 批 + Build & Test。**

---

## 2. 交付后增量 TASK 15 — BI 看板重构（已批准）

> 范围：按当前单店/租户统一聚合店铺经营、推广费用、员工工作量和单款表现；删除点赞量；复用统一时间范围筛选并支持 day/week/month/year。
> 口径：合作日期闭区间；总佣金=全部有效推广报价；推广总花费=已发布报价；未发文=未发布报价；取消金额=已取消报价；单款站外花费仅计已发布；ROI=(销售额-退款)/(站内+站外花费)，分母为 0 返回 null；无数据库迁移。
> 兼容：保留 `/api/reports/bi` 原 `cards/charts` 字段并新增结构化 sections；金额继续序列化为字符串；权限保持 `report.production:read`。

### Increment 15.1 — 计划与设计约束
- [x] 15.1.1 读取需求、BI 前后端调用链、工作进度/店铺/投产聚合、测试、AI-DLC 状态和技术债文件。
- [x] 15.1.2 执行 ui-ux-pro-max 设计系统与 React 栈检索，采用 Ant Design 亮色数据密集型后台、响应式 KPI 卡、可访问筛选和稳定表格交互。
- [x] 15.1.3 固化已批准的数据口径、兼容策略、租户边界和验证步骤。

### Increment 15.2 — 服务端统一聚合
- [x] 15.2.1 新增 BI 结构化 Schema 与 Repository，聚合店铺经营、推广费用、任意日期范围员工工作量、趋势桶和单款已发布站外花费。
- [x] 15.2.2 重构 BiService，集中计算比例/ROI、单款表现和兼容 cards/charts，避免前端跨接口重复口径。
- [x] 15.2.3 扩展 `/api/reports/bi` 支持 `granularity=day|week|month|year` 与明确 response_model，并保持旧请求默认 day。

### Increment 15.3 — 前端三大区域
- [x] 15.3.1 增加 BI TypeScript DTO 与单接口 API 调用。
- [x] 15.3.2 重构 BiDashboardPage，复用 ReportTimeRangeFilter，支持自定义日期和四粒度，删除点赞量。
- [x] 15.3.3 响应式展示店铺/推广汇总、员工工作量、单款表现与趋势；主图提供 alt，表格支持横向滚动和加载/空状态。

### Increment 15.4 — 指标与本地验证
- [x] 15.4.1 在 `指标字典.md` 补齐 BI 指标来源、状态、日期、公式、除零和权限口径。
- [x] 15.4.2 对改动文件执行 diagnostics、frontend build/type-check、Python compileall、ruff、现有 BI/报表 pytest、migration null-byte/AST、`git diff --check`。
- [x] 15.4.3 修复验证发现的问题，并确认工作区不包含用户删除的 Word 临时文件之外的意外改动。

> 本地验证说明：diagnostics、frontend build、compileall、31 个 migration null-byte/AST、`git diff --check` 通过；pytest 被本机缺少 `boto3` 阻断于 conftest 导入，ruff 未安装，独立 type-check 被项目既有 tsconfig node 引用配置阻断；未安装依赖、未新增测试文件。

### Increment 15.5 — 发布、线上验收与清理
- [x] 15.5.1 提交并推送 main，等待 Zeabur frontend/backend 部署 RUNNING，验证 `/ready` DB/Redis。
- [x] 15.5.2 通过真实登录表单验收 BI 自定义日期、四粒度、三大区域、无点赞量、网络 200 和控制台无 error。
- [x] 15.5.3 清理测试数据（若产生），更新 `aidlc-state.md`，仅追加 `audit.md` 并提交收尾文档。

> 2026-08-01 最终验收：实现提交 `00f8552` 已推送 `origin/main`；backend `6a6bf4319cd65e28a342fbf6`、frontend `6a6bf42f9cd65e28a342fbf4` 均为 RUNNING，`/ready` 的 DB、Redis 均为 ok。用户通过真实登录表单进入系统；BI 默认请求与 day/week/month/year 均为 200；自定义日期在范围未完成时不发请求，完成后自动 refresh 并重试 200；响应包含全部结构化字段及兼容 `cards/charts`。页面包含四个目标区域、不含点赞量；375px/768px 无页面横向溢出，表格内部可横向滚动；刷新后干净网络无 4xx/5xx、控制台无 error、backend runtime logs 为空。本轮未创建生产测试数据，无需清理。

---

**当前执行已批准的交付后增量 TASK 15；所有完成项必须在同一轮更新 checkbox。**
