# U08 NFR 设计计划（NFR Design Plan）

> 单元：U08 — 发文进度看板
> 范围：U08 增量设计模式 P-U08-01~03（TimeRange 解析 + 聚合编排 / FILTER+CASE 聚合 SQL / safe_div null 后处理）；其余继承 U01-U07
> 节奏：NFR Design 阶段 = 本计划 + 2 文档（nfr-design-patterns.md + logical-components.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 聚合放 repository 还是 metric
- [Answer] 聚合 SQL 放 `modules/report/repository.py`（PublishProgressRepository，4 个查询方法返回 RowMapping）；`services/metric/publish_progress.py` 只放轻量装配/折算辅助（或并入 service）；`common.safe_div` 独立。service 负责 TimeRange 解析 + 调 repo + safe_div 组装读模型。

### Q2 — 折算 CASE 系数注入
- [Answer] 代码生成时由 `PLATFORM_LIKE_COEFFICIENT`（U04 legacy_settings）动态拼 `CASE WHEN platform IN (<系数<1 平台>) THEN like_count*<系数> ...`，避免硬编码漂移；MVP 仅抖音/快手 ×0.1。

### Q3 — 半月 bucket SQL
- [Answer] `to_char(cooperation_date,'YYYY-MM') || (CASE WHEN extract(day FROM cooperation_date)<=15 THEN ' 上半月' ELSE ' 下半月' END)` 作 bucket key；GROUP BY bucket + period_start（min date）排序。

### Q4 — URGE_EXPR 注入
- [Answer] 复用 U04 `URGE_STATUS_SQL_EXPR`（含 :today/:urge_days/:important_days 参数）；summary/cards 的超时量用 `COUNT(*) FILTER (WHERE (URGE_EXPR)='超时')`，参数随查询注入。

### Q5 — 读模型 level 着色
- [Answer] service 计算 publish_rate/overdue_rate 后附 level（green/yellow/red，BR-U08-32 阈值硬编码常量）；rate=None → level=None。

### Q6 — 异常类型
- [Answer] `modules/report/exceptions.py`：ReportInvalidTimePresetError(422) / ReportInvalidTimeRangeError(422) / ReportStyleNotFoundError(404)，继承 AppException。

### Q7 — 端点前缀
- [Answer] `/api/reports/publish-progress/{summary,cards}` + `/api/reports/publish-progress/styles/{style_id}/{by-pr,by-time}`，全 GET + report.publish_progress:read。

---

## 2. 执行步骤

- [x] 2.1 `U08/nfr-design/nfr-design-patterns.md`：P-U08-01 TimeRange 解析+编排 / P-U08-02 聚合 SQL（summary/cards/detail FILTER+CASE+URGE_EXPR 完整伪代码）/ P-U08-03 safe_div null 后处理 + level 着色 + 一致性校验
- [x] 2.2 `U08/nfr-design/logical-components.md`：modules/report 8 文件 + services/metric（common+publish_progress）+ main 改动（无 migration）+ 依赖图 + 复用清单 + 测试组件
- [x] 2.3 诊断器无警告 + 与 functional-design / nfr-requirements 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 设计文档。**
