# U11 基础设施设计计划（Infrastructure Design Plan）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 范围：migration 015（ALTER 1 列）+ Celery Beat 选装；零新服务/表/桶/依赖/环境变量
> 节奏：Infrastructure Design 阶段 = 本计划 + 2 文档，同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增服务 / 桶 / 依赖 / 环境变量
- [Answer] 全部**零新增**。

### Q2 — 数据库变更
- [Answer] migration 015（接 014）：`ALTER TABLE blogger ADD COLUMN audience_profile JSONB NULL`；无新表 / 无回填 / 无 DDL 限制。

### Q3 — Celery Beat
- [Answer] 选装（默认注释）：`"recompute-blogger-tags": {"task": "tasks.blogger_tasks.recompute_all_blogger_tags", "schedule": crontab(hour=2, minute=0)}`。admin 手动触发为主（POST /api/bloggers/recompute-tags）。

### Q4 — 部署
- [Answer] 代码 + migration 015 同批；celery_app autodiscover 追加路径 `tasks.blogger_tasks`。

### Q5 — 回滚
- [Answer] migration 015 downgrade DROP COLUMN audience_profile；代码回滚移除 tag_service / recompute 端点 / Celery 任务。

---

## 2. 执行步骤

- [x] 2.1 `U11/infrastructure-design/infrastructure-design.md`
- [x] 2.2 `U11/infrastructure-design/deployment-architecture.md`
- [x] 2.3 诊断器无警告（infrastructure-design.md spec-format 假阳性 IGNORE）

---

**等待用户"继续"；本轮直接生成 2 份文档。**
