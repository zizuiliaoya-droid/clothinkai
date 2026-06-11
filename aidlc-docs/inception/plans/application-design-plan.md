# 应用设计计划（Application Design Plan）

## 概述

本计划描述如何生成全栈系统的应用层设计。需求第 11 节已经定义了核心数据实体清单和约束，本阶段聚焦于：
1. 把"业务模块"映射为"代码组件"（含分层）
2. 定义组件接口（方法签名级，不展开业务规则）
3. 设计服务层编排模式
4. 明确组件依赖与通信

**注意**：业务规则的详细设计（如状态机回退副作用、催发计算公式细节）放到 Construction 阶段的 Functional Design 处理。

请阅读"决策问题"并填写所有 `[Answer]:` 标签，然后告诉我"已填好"。

---

## 第一部分：决策问题

### Question 1 — 后端分层架构
FastAPI 后端的分层方案？

A) **三层**：Router → Service → Repository（Repository 直接访问 ORM）
B) **四层**：Router → Service → Domain → Repository（Domain 持纯业务对象，Service 编排）
C) **简化两层**：Router → Service（Service 直接用 SQLAlchemy）
D) Other (请在 [Answer]: 后说明)

[Answer]: B

### Question 2 — 组件粒度对应
代码组件如何对应业务模块？

A) **按 Epic 一对一**：1 Epic = 1 模块包（如 `app/modules/promotion/`），每包内含自己的 router/service/repository
B) **按业务域聚合**：把强相关 Epic 合并（如 EP02+EP03 = product 包，EP04+EP05+EP06 = pr_finance 包）
C) **按层水平拆**：所有 router 在 `app/api/`、所有 service 在 `app/services/`，按文件区分模块
D) Other

[Answer]: A

### Question 3 — 前端组件组织
React 前端组织方式？

A) **按页面**：`pages/Promotions/`、`pages/Settlements/`...，每个页面下含子组件
B) **按 Epic + 共用**：`features/promotion/`、`features/settlement/` + `components/`（共用） + `pages/`（路由壳）
C) **类型分离**：`pages/`、`components/`、`hooks/`、`services/`，扁平组织
D) Other

[Answer]: B

### Question 4 — 状态管理
前端状态管理方案？

A) **Zustand**（轻量，开发文档已提及）+ React Query 处理服务端状态
B) **Redux Toolkit** + RTK Query
C) **仅 React Context + useState**（最轻）
D) Other

[Answer]: A

### Question 5 — 异步任务边界
哪些操作走 Celery 异步队列，哪些走同步 API？

A) **明确清单**：定时任务（采集、催发扫描、备份）+ 长任务（导入解析、企微群发、报表预聚合）走 Celery；其他都同步
B) **导入相关都走 Celery**，其他同步
C) **能异步则异步**（包括所有写操作）
D) Other（请说明哪些走异步）

[Answer]: A

### Question 6 — 多租户实施位置
多租户隔离的代码实施层？

A) **ORM Session 层注入** tenant_id（FastAPI 中间件读取 JWT 后设置 Session 属性，所有查询自动带 tenant_id）+ PostgreSQL RLS 兜底
B) **仅 ORM Session 注入**（不开 RLS，性能优先）
C) **仅 PostgreSQL RLS**（数据库强制，应用层不管）
D) Other

[Answer]: A

### Question 7 — 字段级权限实施位置
字段级权限的代码实施层？

A) **Pydantic Response Schema 动态字段过滤**（按用户角色返回不同字段集）
B) **ORM 查询时按权限选字段**（性能更好但复杂）
C) **响应后中间件过滤**（输出前统一处理）
D) **Pydantic 动态 Schema + 装饰器组合**（A + 装饰器封装）
E) Other

[Answer]: D

### Question 8 — 状态机实施方式
推广合作 / 设计制版 / 财务结款 三个状态机如何实现？

A) **手写 if/elif 状态转移函数**（最简单）
B) **transitions 库**（声明式状态机）
C) **领域模型方法 + 显式状态转移表**（方法名即转移：`promotion.publish()` / `promotion.cancel()`，转移表在常量文件）
D) Other

[Answer]: C

### Question 9 — 指标计算服务
报表指标（CPL、净投产比、爆文率等）的代码组织？

A) **MetricService 单独包**：`app/services/metric/`，每个指标一个函数，可按租户/时间/款式调用
B) **散布在各 Service**（推广指标在 PromotionService、报表指标在 ReportService）
C) **数据库视图 / 物化视图为主，Service 只做组装**
D) Other

[Answer]: A

### Question 10 — 实时催发状态计算
催发状态（urge_status）按需求是"实时计算不存库"，怎么实现？

A) **Python Service 层方法**（查询时调用 `compute_urge_status(promotion)` 返回）
B) **SQLAlchemy hybrid_property**（声明在模型上，ORM 自动算）
C) **PostgreSQL 数据库函数 / 视图**（DB 层算，性能好）
D) **混合**：Service 层为主（A），列表查询用 SQL 表达式优化（C）
E) Other

[Answer]: D

### Question 11 — 文件附件与 R2 集成
附件（图片、付款截图、制版文件）的代码组织？

A) **专用 AttachmentService**：统一封装上传/下载/签名 URL 逻辑，业务表存 attachment_id 而非直接 URL
B) **业务表直接存 url 字段**（简单）
C) **AttachmentService + 业务表存 attachment_id + 公开/私有桶分离**（推荐安全模式）
D) Other

[Answer]: C

### Question 12 — 企微集成边界
企微 API 集成应该是？

A) **WecomClient（基础 SDK）+ WecomService（业务编排）+ WecomTask（Celery 任务）三层**
B) **单一 WecomService 包揽所有**
C) **WecomClient + 直接在业务 Service 里调用**
D) Other

[Answer]: A

### Question 13 — 采集 Worker 与主系统通信
采集 Worker 在外部执行机，怎么和主系统通信？

A) **Worker 完成后 HTTP 调用主系统的 `/api/import/upload`**（Worker 当成普通客户端）
B) **共享 Celery 队列**：主系统派发采集任务到队列，Worker 订阅执行后通过队列回写结果
C) **混合**：调度通过 Celery 队列；数据回传走 HTTP API
D) Other

[Answer]: D，主系统提供采集任务 API，Worker 轮询/领取任务，完成后通过 HTTP 上传文件和回写状态。

### Question 14 — 审计日志写入方式
audit_log 怎么写？

A) **装饰器自动记**（在敏感 API 上加 `@audit("decrypt")` 装饰器）
B) **业务代码显式调用 AuditService.log(...)**
C) **SQLAlchemy 事件钩子**（监听 INSERT/UPDATE 自动写）
D) **混合**：API 级用装饰器（A），ORM 级关键表用钩子（C）
E) Other

[Answer]: D

### Question 15 — 测试组织
测试代码怎么组织？

A) **按生产代码镜像**：`tests/api/`、`tests/services/`、`tests/repositories/`
B) **按测试类型**：`tests/unit/`、`tests/integration/`、`tests/api/`
C) **每个模块自带测试**：`app/modules/promotion/tests/`
D) Other

[Answer]: B

---

## 第二部分：执行清单（待批准后执行）

> ⚠️ 以下步骤在用户批准本计划后执行。每完成一步立即标记 `[x]`。

### A. 组件识别
- [ ] A1. 基于 Q2 答案确定模块包结构（后端）
- [ ] A2. 基于 Q3 答案确定前端组件组织
- [ ] A3. 列出每个组件的核心职责与接口
- [ ] A4. 写入 `aidlc-docs/inception/application-design/components.md`

### B. 组件方法签名
- [ ] B1. 为每个 Service/Repository 定义关键方法签名（不展开业务规则）
- [ ] B2. 标注每个方法的输入/输出类型
- [ ] B3. 关联到对应故事 ID（可追溯性）
- [ ] B4. 写入 `aidlc-docs/inception/application-design/component-methods.md`

### C. 服务层设计
- [ ] C1. 定义跨组件编排的 Service（结算流程、催发触发、采集任务调度等）
- [ ] C2. 标注每个 Service 的事务边界
- [ ] C3. 异步 vs 同步标注（基于 Q5）
- [ ] C4. 写入 `aidlc-docs/inception/application-design/services.md`

### D. 组件依赖关系
- [ ] D1. 生成组件依赖矩阵（行=组件、列=依赖的组件）
- [ ] D2. 生成模块依赖图（Mermaid）
- [ ] D3. 标注通信模式（同步函数调用 / Celery 任务 / DB 事件）
- [ ] D4. 验证不存在循环依赖
- [ ] D5. 写入 `aidlc-docs/inception/application-design/component-dependency.md`

### E. 横切关注点设计
- [ ] E1. 多租户隔离实施方案（基于 Q6）
- [ ] E2. 字段级权限实施方案（基于 Q7）
- [ ] E3. 状态机封装方案（基于 Q8）
- [ ] E4. 审计日志写入方案（基于 Q14）
- [ ] E5. 这些方案在 components.md 的 "横切组件" 章节体现

### F. 与 Units 单元的映射
- [ ] F1. 在每份设计文档中标注组件归属哪些工作单元（U01-U18）
- [ ] F2. 验证每个 MVP 单元的依赖组件都已设计完整
- [ ] F3. 验证 V1 字段级权限单元（U09）的实施位置已固化

### G. 一致性校验
- [ ] G1. 所有组件 ID 唯一
- [ ] G2. 所有方法签名输入/输出类型明确
- [ ] G3. 没有循环依赖
- [ ] G4. 横切关注点（多租户/字段级权限/状态机/审计）方案一致
- [ ] G5. 89 个可实施故事都至少有一个组件方法支持

### H. 状态更新
- [ ] H1. 更新 `aidlc-state.md`，标记 Application Design 阶段完成
- [ ] H2. 在 `audit.md` 记录生成完成时间戳
