# U17 基础设施设计计划（Infrastructure Design Plan）

> 单元：U17 — 套装 + BI 看板 + 报表导出（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 产出：infrastructure-design.md + deployment-architecture.md
> 增量式：复用 U01/U02/U14 基础设施（Zeabur 6 服务 + RLS + 审计 + openpyxl）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否新增 Zeabur 服务 / 计算资源？
[Answer] 无新服务/进程。复用 backend（bundle/BI/导出 API）。无 Celery 任务/Beat（U17 同步只读 + 写 bundle/偏好）。导出为同步 HTTP 流式响应。

### Q2：数据库变更？
[Answer] migration 021：bundle_product + bundle_item + user_preference 3 表（RLS + idx + CHECK + UNIQUE）+ product.bundle/report.export scope seed（merchandiser/pr_manager/operations + admin 通配）。down 安全 drop 3 表 + 删 scope。无回填。

### Q3：新增依赖 / 环境变量 / R2 / Redis？
[Answer] 全部零新增。导出复用 openpyxl==3.1.5（U06a 已装）；BI 复用 U14 report service；无 R2/Redis 新用量；无环境变量。

### Q4：导出响应特性（部署面）？
[Answer] 导出返回 StreamingResponse（xlsx 二进制流）；backend 服务无需额外配置；Zeabur/反向代理需允许二进制响应 + Content-Disposition（默认支持）。导出无异步任务，受 HTTP 超时约束（V2 报表量级 ≤3s，远低于超时）。

### Q5：部署一致性？
[Answer] U17 依赖 U02（product/sku）+ U14（report service）已部署（MVP/V1 完成）。migration 021 紧接 020。bundle/user_preference 新表无对历史数据影响。BI/导出只读复用既有 service，无破坏性变更。

### Q6：监控？
[Answer] 复用 U01 prometheus /metrics（report_export_total 自动暴露）+ U05/U02 审计（bundle 创建留痕）。无新监控基础设施。

### Q7：本地验证环境？
[Answer] Docker PG16:5560 + Redis7:6415 + Py3.12（U17 唯一端口）；alembic upgrade head 含 021；U17 子集 + 全量回归；覆盖率 ≥70%。

### Q8：回滚策略？
[Answer] 代码：移除 bundle_router/bi_router/export_router。DB：migration 021 down（drop 3 表 + 删 4 scope），无外键被引用，安全。

---

## 1. 步骤

- [x] 1.1 阅读 U01/U02/U14 infrastructure-design（Zeabur 服务/RLS/审计）+ U17 nfr-design logical-components（migration 021）
- [x] 1.2 编写 infrastructure-design.md（无新服务；migration 021 3 表 + scope seed；零新依赖/环境变量/R2/Redis；导出流式响应部署面；部署一致性；本地 Docker 5560/6415）
- [x] 1.3 编写 deployment-architecture.md（拓扑无变更+部署 checklist+验证步骤+监控+回滚）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。infrastructure-design.md 的 spec-format 假阳性 IGNORE。**
