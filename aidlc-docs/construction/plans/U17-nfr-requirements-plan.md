# U17 NFR 需求计划（NFR Requirements Plan）

> 单元：U17 — 套装 + BI 看板 + 报表导出（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 增量式：复用 U01 NFR 基线 + U02 product NFR + U14 report SLA + openpyxl（U06a）
> 依赖：U02（product/sku）、U14（report service）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：套装/BI/导出 API 性能 SLA？
[Answer] bundle create/get P95 ≤ 200ms（单 bundle + N items）；BI get_dashboard ≤ 1s（3 个 report service 聚合，复用各自 SLA）；导出 work-progress/store-daily ≤ 2s、production ≤ 3s（跨表 + openpyxl 序列化，流式不超内存）。

### Q2：导出内存与流式安全？
[Answer] openpyxl write_only Workbook（lxml 增量写）+ BytesIO；避免一次性加载全部 cell 对象。报表行数 V2 量级（千级）可控；StreamingResponse 返回。导出无分页全量但有时间筛选限制跨度 ≤366 天。

### Q3：BI 聚合性能？
[Answer] get_dashboard 串行调用 3 个 report service（WorkProgress/Production/StoreDaily）；总耗时 ≈ 各 SLA 之和（≤1s 目标）。V2 不做 precompute 缓存（数据量可控）；布局偏好读写单行 O(1)。

### Q4：安全 / 威胁模型？
[Answer] bundle/user_preference RLS + 显式 tenant；bundle_item.sku 跨租户校验；user_preference 本人 user_id 隔离（他人不可读写）；导出 require_permission report.export:read（403）；导出文件名/内容不含跨租户数据（RLS 兜底）。

### Q5：并发 / 一致性？
[Answer] bundle create UNIQUE(tenant,bundle_code) + IntegrityError→409；user_preference upsert ON CONFLICT(tenant,user,key)；导出只读无并发问题；split_quantities 纯函数无副作用。

### Q6：可观测指标？
[Answer] 新增可选指标 report_export_total{report_type,result}（导出次数/结果）。BI/bundle 复用 U01 metrics + 审计。

### Q7：迁移与回滚？
[Answer] migration 021：bundle_product + bundle_item + user_preference 3 表（RLS + idx + CHECK + UNIQUE）+ product.bundle/report.export scope seed。down 安全 drop 3 表 + 删 scope。无回填。

### Q8：依赖？
[Answer] 零新依赖：复用 openpyxl==3.1.5（U06a）+ U14 report service + U02 product。

### Q9：测试矩阵？
[Answer] 测试 3 文件：unit（split_quantities 拆分逻辑 + 导出行序列化纯函数）+ integration（bundle create + item 校验 + split + user_preference upsert + 导出生成 xlsx 字节流可解析 + RLS）+ api（bundles + bi + export 401/403 + OpenAPI）。

### Q10：报表导出权限边界？
[Answer] report.export:read 独立 scope（pr_manager/operations/admin）；区别于 report.*:read（仅查看）。无导出权限调用 export → 403（EP09-S08 第二条）。BI 看板用 report.*:read（运营已有）。

---

## 1. 步骤

- [x] 1.1 阅读 U17 functional-design 3 文档 + U02 product NFR + U14 report SLA + openpyxl write_only 用法 + U01 NFR 基线
- [x] 1.2 编写 nfr-requirements.md（性能 SLA + 导出流式内存 + BI 聚合 + 威胁模型 + 并发 + 1 指标 + migration 021 + 测试矩阵）
- [x] 1.3 编写 tech-stack-decisions.md（零新依赖复用 openpyxl/report service/product；modules 11 新建 + 7 横切落点；openpyxl write_only 流式片段；report_export_total 指标；migration 021 片段；测试 3 文件）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。nfr-requirements.md 的 spec-format 假阳性 IGNORE。**
