# U11 NFR 设计计划（NFR Design Plan）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 范围：2 增量模式 + 组件清单
> 节奏：NFR Design 阶段 = 本计划 + 2 文档，同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 模式数量
- [Answer] 2 个：**P-U11-01**（BloggerTagService 实时+批量计算 + safe_div + 阈值常量 + Celery recompute 逐 tenant 容错）；**P-U11-02**（audience_profile 读展示 + read_like_ratio 读时衍生 + 聚合 avg_cpl/hit_rate 显式 tenant + LIMIT 截断）。

### Q2 — recompute 容错
- [Answer] 逐 blogger try/except → log.warning + 继续；返回 (updated, failed) 计数；Celery autoretry 2 次（OperationalError）。

### Q3 — 实时触发集成点
- [Answer] BloggerService.create_blogger / update_blogger 中若 follower_count 变更 → 同步调 BloggerTagService.compute_and_save_type(blogger)；不改 BloggerService 签名，仅追加调用。

### Q4 — BloggerResponse 扩展
- [Answer] 追加 audience_profile: dict|None + read_like_ratio: Decimal|None；ratio 在 service _to_response 中现算（BloggerTagService.compute_read_like_ratio）。

### Q5 — migration 015
- [Answer] ALTER blogger ADD audience_profile JSONB NULL；downgrade DROP COLUMN；无新表。

---

## 2. 执行步骤

- [x] 2.1 `U11/nfr-design/nfr-design-patterns.md`：P-U11-01 + P-U11-02 完整伪代码
- [x] 2.2 `U11/nfr-design/logical-components.md`：新建/修改文件清单 + migration 015 + Celery + 测试 + 依赖图
- [x] 2.3 诊断器无警告

---

**等待用户"继续"；本轮直接生成 2 份文档。**
