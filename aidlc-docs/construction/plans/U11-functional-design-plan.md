# U11 功能设计计划（Functional Design Plan）

> 单元：U11 — 博主智能标签 + 灰豚展示（EP04-S04~S08）
> 依赖：U03（Blogger model 已有 blogger_type/quality_tags/is_suspected_fake）、U13（灰豚数据写入，U11 仅读）
> 节奏：Functional Design 阶段 = 本计划 + 3 文档，同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — BloggerTagService 落点
- [Answer] 新建 `modules/blogger/tag_service.py`（与 U03 BloggerService 同模块，不同文件，解耦标签重算逻辑）；替代 BloggerService 中 4 个 NotImplementedError 占位方法。

### Q2 — 阈值存储
- [Answer] MVP/V1 阶段阈值以代码常量（`modules/blogger/tag_config.py`）定义（follower 分界 / 假号 ratio / 质量 CPL 等）。未来可迁 DB 字典（不在本单元，记演化）。阈值修改 = 代码变更 + 重新部署 + 触发 recompute Celery 任务。

### Q3 — 实时 vs 异步
- [Answer] **实时计算**（blogger 创建/更新 follower_count 时立即算 blogger_type；read_like_ratio / quality_tags 按需计算 = 读时查）；**批量异步重算**（阈值变更后管理员手动触发 Celery 任务 `recompute_all_blogger_tags`，逐 tenant 遍历 active bloggers 更新）。

### Q4 — audience_profile（灰豚画像，S08）
- [Answer] Blogger 表追加 `audience_profile JSONB`（nullable）；由 U13 灰豚 adapter 写入（U11 仅读展示）；BloggerResponse 追加 audience_profile 字段。migration 015 ALTER blogger ADD audience_profile。

### Q5 — read_like_ratio 数据源
- [Answer] V1 阶段：`services/metric/blogger_quality.py` 从 blogger 的 `audience_profile.note_stats` 中提取近期阅读量/点赞量（灰豚同步）。若灰豚未同步（audience_profile=null）→ ratio = null。未来 U13 Worker 定期刷新。

### Q6 — quality_tags 计算依赖
- [Answer] 计算逻辑在 `services/metric/blogger_quality.py`：高性价比标签 = 博主历史推广平均 CPL ≤ 阈值（需查 promotion 表 aggregation）；带货型 = 博主发布推广命中率 ≥ 阈值。依赖 U04 promotion 表。

### Q7 — 触发时机
- [Answer] blogger_type：blogger 创建/更新 follower_count 时 BloggerTagService.compute_and_save_type 同步写入。is_suspected_fake + quality_tags：Celery 任务 `recompute_all_blogger_tags`（admin 手动触发 / Beat 每日 02:00 选装）逐 blogger 计算并 UPDATE。GET detail 时 read_like_ratio 现算（不存 DB，纯衍生）。

### Q8 — 新增依赖
- [Answer] 零新增运行时依赖。

### Q9 — migration
- [Answer] migration 015（接 014）：ALTER blogger ADD audience_profile JSONB NULL；无新表（利用 U03 已有字段 blogger_type/quality_tags/is_suspected_fake）。

### Q10 — 权限
- [Answer] 复用 blogger.*:*（BloggerTagService 是内部计算组件无端点）；recompute 任务由 admin 手动触发（admin Celery 或 API 端点 POST /api/bloggers/recompute-tags 鉴权 admin）。

---

## 2. 执行步骤

- [x] 2.1 `U11/functional-design/domain-entities.md`：audience_profile JSONB 字段 + 阈值常量 + 衍生字段 read_like_ratio + BloggerTagService 算法输入/输出
- [x] 2.2 `U11/functional-design/business-rules.md`：BR-U11-01~ blogger_type 阈值 + read_like_ratio 分母 0→null + is_fake 判定 + quality_tags 多标签 + recompute 批量 + audience_profile 展示/null + 触发时机 + 权限
- [x] 2.3 `U11/functional-design/business-logic-model.md`：UC（compute_type/compute_ratio/is_fake/quality_tags/recompute_all + detail 展示 audience_profile）+ 跨单元契约（U03 model/U04 promotion/U13 灰豚写入）
- [x] 2.4 诊断器无警告 + 与 EP04-S04~S08 一致

---

**等待用户"继续"；本轮直接生成 3 份功能设计文档。**
