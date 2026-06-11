# U08 部署架构（Deployment Architecture）

> 单元：U08 — 发文进度看板
> 结论：随 backend 镜像部署，无独立部署步骤

---

## 1. 部署增量 Checklist

- [ ] 代码随 backend 镜像构建发布（report_router 已在 main.py 注册）
- [ ] 确认 4 GET 端点可达：`/api/reports/publish-progress/{summary,cards}` + `/styles/{id}/{by-pr,by-time}`
- [ ] **无 migration**（不执行 alembic 新版本；head 仍 011）
- [ ] **无环境变量改动**
- [ ] 权限确认：`report.publish_progress:read` 已 seed 给 pr / pr_manager / operations（U04/U07 既有）

---

## 2. 部署流程

- 与 backend 现有流程一致（main 自动部署 prod / PR 预览 staging）。
- U08 不引入新服务、新 secret、新 migration job，部署即随 backend 镜像生效。

---

## 3. 本地开发

- 复用现有 docker-compose；无新增依赖 / 环境变量。
- 测试构造 promotion + style 数据集即可验证看板聚合（无外部依赖）。

---

## 4. 回滚

- 代码回滚：Zeabur 多版本切换（U01 既有）。
- 无 migration → 无数据回滚；纯读端点回滚无副作用。

---

## 5. 监控

- HTTP 时延由 prometheus-fastapi-instrumentator 自动暴露（/api/reports/* handler 分组）。
- structlog 记 report 查询 tenant_id / preset / 范围 / 耗时。
- Sentry：聚合 SQL 异常 capture。
- 告警（V1 Grafana）：/api/reports/* P95 > 500ms 持续 → 评估索引/物化视图。

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 随 backend 镜像部署（无独立服务） | ✅ §2 |
| 无 migration / 环境变量 | ✅ §1 |
| 权限已 seed | ✅ §1 |
| 回滚 = 代码回滚（无数据副作用） | ✅ §4 |
| HTTP 时延监控 | ✅ §5 |
