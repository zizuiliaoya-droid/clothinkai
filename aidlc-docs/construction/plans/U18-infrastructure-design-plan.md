# U18 基础设施设计计划（Infrastructure Design Plan）

> 单元：U18 — AI 决策建议（EP11-S01、S02、S03）（P3 项目收官单元）
> 产出：infrastructure-design.md + deployment-architecture.md
> 增量式：复用 U01 基础设施（Zeabur 6 服务 + RLS + Sentry）+ U07 外部出站模式

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否新增 Zeabur 服务 / 计算资源？
[Answer] 无新服务/进程。复用 backend（AI API 同步）。无 Celery 任务/Beat（ai_advisory_request 占位 P3 不启用）。

### Q2：数据库变更？
[Answer] migration 022：ai_advice_log 1 表（RLS + idx）+ ai.advice:read scope seed（pr/pr_manager/operations + admin 通配）。down 安全 drop 表 + 删 scope。无回填。

### Q3：新增依赖 / 环境变量 / R2 / Redis？
[Answer] 零新依赖（复用 httpx）。新增环境变量 DEEPSEEK_API_BASE / DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_TIMEOUT（Zeabur Secrets，API_KEY 敏感）。无 R2 用量；无 Redis 新用量（P3 无缓存）。

### Q4：外部出站（部署面）？
[Answer] 出站到 DeepSeek（api.deepseek.com）HTTPS /chat/completions；backend 需可访问外网（Zeabur 香港节点可访问）。未配置 API_KEY 时全 503 降级，不出站。超时 30s。

### Q5：部署一致性？
[Answer] U18 依赖 U14（report）/U15（wecom_alert_log）/U03（blogger）/U02（product）均已部署。migration 022 紧接 021。AI 模块独立，DeepSeek 不可用不影响其余模块与启动。

### Q6：监控（NFR）？
[Answer] 复用 U01 prometheus /metrics（ai_advice_total + ai_advice_latency_seconds 自动暴露）+ Sentry（非降级类异常 capture，降级 503 不 capture）。ai_advice_log 用量/成本追踪。

### Q7：本地验证环境？
[Answer] Docker PG16:5561 + Redis7:6416 + Py3.12（U18 唯一端口）；alembic upgrade head 含 022；U18 子集 + 全量回归；覆盖率 ≥70%；DeepSeek 全程 monkeypatch（不调真实 API）。

### Q8：回滚策略？
[Answer] 代码：移除 ai_router 挂载。DB：migration 022 down（drop ai_advice_log + 删 scope）。配置：移除 DEEPSEEK_* env（或留空 → 全 503 降级）。无外键被引用，安全。

### Q9：成本与密钥安全（部署）？
[Answer] DEEPSEEK_API_KEY 存 Zeabur Secrets（不入仓库/日志）；ai_advice_log 记 latency/status 便于成本监控；数据不足/无候选不调 AI 控成本。

---

## 1. 步骤

- [x] 1.1 阅读 U01/U07 infrastructure-design（Zeabur 服务/Secrets/出站/Sentry）+ U18 nfr-design logical-components（migration 022）
- [x] 1.2 编写 infrastructure-design.md（无新服务；migration 022 1 表 + scope seed；零新依赖 + DEEPSEEK_* env(Secrets)；DeepSeek 出站部署面；部署一致性；本地 Docker 5561/6416）
- [x] 1.3 编写 deployment-architecture.md（拓扑无变更+部署 checklist+验证步骤+监控+回滚+密钥安全）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。infrastructure-design.md 的 spec-format 假阳性 IGNORE。**
