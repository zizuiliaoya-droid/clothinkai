# U11 NFR 需求计划（NFR Requirements Plan）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 范围：U11 特异 NFR（批量重算性能 / Celery 任务 / 衍生计算安全 / 多租户隔离）；通用 NFR 继承 U01-U10b
> 节奏：NFR Requirements 阶段 = 本计划 + 2 文档，同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增依赖
- [Answer] **零新增运行时依赖**：纯 Python 计算 + SQLAlchemy 聚合 + Celery 复用。

### Q2 — 批量重算性能
- [Answer] 逐 tenant 逐 blogger 串行计算（低优先级后台任务，不影响在线 SLA）；单 blogger 计算 P95 ≤ 50ms（内存 + 1-2 次 DB 聚合）；1 万 blogger / tenant 全量重算约 5-10 min（可接受，Beat 凌晨跑）。

### Q3 — Celery 任务
- [Answer] 新增 1 个 Celery 任务 `recompute_all_blogger_tags`（注册在 `tasks/blogger_tasks.py`；复用 `system_context` 逐租户；Celery autodiscover）。Beat 每日 02:00 选装（配置项，默认关闭，admin 手动触发为主）。

### Q4 — 衍生字段安全
- [Answer] read_like_ratio 纯内存计算（audience_profile JSONB 已在 blogger 行内），无额外查询；不信任客户端传入 ratio。quality_tags 计算需聚合 promotion 表（avg CPL / hit_rate），query 限 ≤ 1000 条（LIMIT，超出截断）。

### Q5 — 多租户
- [Answer] recompute 逐 tenant system_context 执行；promotion 聚合显式 WHERE tenant_id（防御 + 测试确定性）；audience_profile 写入由 U13 保证租户隔离。

### Q6 — 安全
- [Answer] recompute 端点仅 admin（* 通配）；标签计算为内部逻辑不暴露单独写 API；audience_profile 由后台写入不接受客户端篡改（BloggerUpdate schema 不含 audience_profile）。

### Q7 — 监控
- [Answer] 不新增自定义 Prometheus 指标；Celery 任务成功/失败计入既有 Celery 指标；structlog 记 recompute 完成（tenant_id / updated_count）。

### Q8 — 测试
- [Answer] 单元：compute_blogger_type 阈值边界 + read_like_ratio 分母 0 + is_fake + quality_tags 多条件 + audience_profile null。集成：recompute 任务全流程 + 实时 type 触发 + promotion 聚合。API：recompute 端点鉴权 + blogger detail 含新字段。

---

## 2. 执行步骤

- [x] 2.1 `U11/nfr-requirements/nfr-requirements.md`
- [x] 2.2 `U11/nfr-requirements/tech-stack-decisions.md`
- [x] 2.3 诊断器无警告 + 与 functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份文档。**
