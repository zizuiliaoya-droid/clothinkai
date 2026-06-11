# 工作单元生成计划（Unit of Work Plan）

## 概述

本计划基于已批准的：
- **执行计划**（execution-plan.md）— 已经定义了 23 个 sub-unit 的初步划分（U01-U18，含 U06a-e 和 U10a-b）
- **应用设计**（components.md / component-methods.md / services.md / component-dependency.md）— 已经把代码组件、方法签名、依赖图全部固化

由于上游决策已经收敛，本阶段不再引入新的决策问题，只需要您**确认 1 个边界问题**，然后直接生成 3 份产物。

---

## 第一部分：边界确认问题

### Question 1 — 单一 Service vs 单一应用 + 模块
执行计划写的是 **23 个 sub-unit** 用于"按阶段交付"，应用设计写的是 **10 个 Epic 模块包**用于代码组织。这两个层次本就是 Units of Work 的不同视角。我会按以下口径生成产物，请确认：

A) **确认**：以"单一 FastAPI 应用 + 多模块 + 多 sub-unit 计划"建模  
   - 部署单元 = 1 个 backend 应用 + celery-worker + celery-beat + frontend（参见执行计划第 6.2 节）
   - 逻辑模块 = 10 个 Epic 包（参见 components.md）
   - 计划单元 = 23 个 sub-unit（参见执行计划第 9 节）  
   - unit-of-work.md 详述这三层关系
B) **重做**：按微服务拆分（每个 sub-unit 是独立服务）
C) Other

[Answer]: A

---

## 第二部分：执行清单（待确认后执行）

### A. 生成 unit-of-work.md
- [ ] A1. 写入"部署模型"小节（单 backend 应用 + 6 服务 + 外部采集 Worker）
- [ ] A2. 写入"代码组织策略"小节（按 Epic 一对一模块包，详见 components.md）
- [ ] A3. 写入"23 个 sub-unit 详述"（每个含名称、阶段、覆盖故事、覆盖代码组件、依赖、验收）
- [ ] A4. 写入"工作单元生命周期"小节（每单元的 Construction 阶段流程）
- [ ] A5. 写入"阶段批次"小节（MVP/V1/V2/P3 各阶段的 sub-unit 集合 + 阶段末验收）

### B. 生成 unit-of-work-dependency.md
- [ ] B1. 单元依赖矩阵（行=单元、列=被依赖单元）
- [ ] B2. 依赖图（Mermaid，按阶段着色）
- [ ] B3. 关键路径标注（MVP 关键路径、V1 关键路径）
- [ ] B4. 阶段内并行机会列表

### C. 生成 unit-of-work-story-map.md
- [ ] C1. 89 个可实施故事 → 23 个 sub-unit 的完整映射表
- [ ] C2. 5 个 Overview 故事 → 对应 Epic 内 sub-unit 的覆盖关系
- [ ] C3. 7 个 NFR Checklist → 对应 sub-unit 的验收门
- [ ] C4. 3 个 P3 故事 → U18 的覆盖关系
- [ ] C5. 反向校验：每个 sub-unit 至少一个故事，每个可实施故事至少一个 sub-unit
- [ ] C6. Epic-S01 Overview 故事的覆盖说明（Overview 的 GWT 由 sub-unit 故事并集承担）

### D. 一致性校验
- [ ] D1. 所有 sub-unit 在 execution-plan.md 中存在
- [ ] D2. 所有故事 ID 在 stories.md 的总览表中存在
- [ ] D3. 依赖图与 execution-plan 第 9 节"推荐单元划分"一致
- [ ] D4. 部署模型与执行计划第 6.2 节一致
- [ ] D5. 模块组织与 components.md 第 1 节一致

### E. 状态更新
- [ ] E1. 更新 aidlc-state.md，标记 Units Generation 完成
- [ ] E2. 在 audit.md 记录生成完成时间戳
