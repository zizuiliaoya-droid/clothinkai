# U06c 功能设计计划（Functional Design Plan）

> 单元：U06c — 博主导入适配器
> 阶段：MVP 第 8 个 sub-unit（导入并行支线，第 2 个业务 Adapter）
> 依赖：**U03（blogger 表 + BloggerRepository.upsert_atomic）+ U06a（ImportAdapter 协议 + Registry + runner）**
> 节奏：单批生成（与 U06b 同构，更简单 —— 单实体，无 style-reuse 类两实体编排）

---

## 0. 单元定位与关键事实

U06c 是 **U06a 框架的第二个业务 Adapter**，与 U06b 高度同构。它不新建表/端点/Celery 任务，仅：
1. 实现 `BloggerImportAdapter`（满足 U06a `ImportAdapter` 协议，FB-C 签名）
2. 注册 `manual_blogger` 来源（main.py 已预置 `adapters.blogger` 模块路径）
3. 定义博主字段映射规则（默认列映射 + 类型转换）
4. 端到端样本 CSV 跑通验收

### 0.1 与 U06b 的差异（更简单）

| 维度 | U06b（商品/SKU） | U06c（博主） |
|---|---|---|
| 目标实体 | 两实体（Style 复用/建 + Sku upsert） | **单实体**（Blogger upsert） |
| 编排复杂度 | style get-or-create + sku upsert 同事务 | **单次 upsert_atomic** |
| 业务键 | sku_code（+ style_code 关联） | **xiaohongshu_id** |
| 关联查询 | brand_code → brand_id（软关联） | **无**（博主无外键关联） |
| 类型转换 | Decimal（3 价格字段） | Decimal（quote）+ Integer（follower_count）+ **JSONB 数组**（category_tags/quality_tags） |

### 0.2 上游既有能力（直接复用）

| 能力 | 来源 | U06c 用法 |
|---|---|---|
| ImportAdapter 协议 / Registry / runner / 8 端点 | U06a | 实现协议 + register manual_blogger |
| `BloggerRepository.upsert_atomic`（ON CONFLICT xiaohongshu_id RETURNING is_inserted） | U03 | blogger 行 upsert |
| register_import_adapters（main.py 已含 adapters.blogger 路径） | U06a | 自动注册 |
| _to_decimal 模式 | U06b | 复用思路（quote 字段） |

---

## 1. 覆盖故事

U06c 与 U06a/b/d/e 共享 **EP07-S07~S10**；额外验收 = 博主目标表字段映射 + 端到端样本 CSV。

| 故事 | U06c 体现 |
|---|---|
| EP07-S07 上传 | upload source=manual_blogger → BloggerImportAdapter |
| EP07-S08 去重 | 复用 U06a 框架层 hash 去重 |
| EP07-S09 映射版本 | manual_blogger 默认映射 v1 |
| EP07-S10 失败下载/重试 | 行级失败（如 xiaohongshu_id 缺失）→ failed → 下载/重试 |

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — source 标识
[Answer] **`manual_blogger`**（与 main.py 预置 `adapters.blogger` 路径一致）。target_table=`blogger`。

### Q2 — 一行的目标实体
[Answer] **一行 = 一个 Blogger**（单实体，无关联）。按 `(tenant_id, xiaohongshu_id)` upsert_atomic。返回 (blogger.id, is_inserted)。

### Q3 — 字段映射（manual_blogger v1 默认列）
[Answer] 默认映射：

| source_col | target_field | required | type | 说明 |
|---|---|---|---|---|
| 小红书ID | xiaohongshu_id | ✅ | str | 业务键 |
| 昵称 | nickname | ✅ | str | |
| 平台 | platform | — | str | 默认"小红书" |
| 微信 | wechat | — | str | |
| 手机号 | phone | — | str | |
| 粉丝数 | follower_count | — | int | ≥0 |
| 博主类型 | blogger_type | — | str | |
| 性别投放 | gender_target | — | str | |
| 类目标签 | category_tags | — | list | 分号/逗号分隔 → JSONB 数组 |
| 质量标签 | quality_tags | — | list | 分号/逗号分隔 → JSONB 数组 |
| 报价 | quote | — | decimal | ≥0 |
| 合作历史 | cooperation_history | — | str | |
| 备注 | remark | — | str | |

### Q4 — 标签字段解析（list 类型，U06c 新增）
[Answer] category_tags / quality_tags 源列为分隔字符串（支持 `;` `；` `,` `，` 分隔）→ 拆分 + strip + 去空 → JSONB 数组。空 → `[]`。U06b 无此类型，U06c 引入 `_split_tags` 纯函数。

### Q5 — 数值类型（follower_count + quote）
[Answer] follower_count：Integer（去千分位 + int，空→None，非法/负数→校验失败）。quote：Decimal（复用 U06b _to_decimal，禁 float，空→None，负数→失败）。

### Q6 — 行校验规则
[Answer] 必填：xiaohongshu_id / nickname。follower_count 非空时 int 且 ≥0；quote 非空时 Decimal 且 ≥0。长度上限（对齐 U03）：xiaohongshu_id≤64 / nickname≤128 / wechat≤64 / phone≤32 / platform≤16 / blogger_type≤16 / gender_target≤16。

### Q7 — upsert 幂等与事务（FB-C）
[Answer] `upsert(parsed, *, session, tenant_id, actor_id)` 不自 commit；单次 `BloggerRepository(session).upsert_atomic(tenant_id, values)`（ON CONFLICT xiaohongshu_id）。返回 (blogger.id, is_inserted)。无 created_by 字段（U03 blogger 无 created_by），actor_id 不写入（仅审计上下文，与 U06b style.owner_id 不同）。

### Q8 — 权限
[Answer] 复用 U06a `importer.batch:read/write` + `importer.mapping:write`（无新 scope）。

### Q9 — 测试策略
[Answer] 真实 BloggerImportAdapter：unit（parse_row 含 _split_tags / int / Decimal + validate 各分支）+ integration（注册 → upload 样本 CSV → runner → blogger 入库 + 标签 JSONB + partial + retry 幂等）。复用 U06a/U06b 测试基建。

### Q10 — 注册时机
[Answer] 复用 U06a register_import_adapters（main.py 已含 adapters.blogger，落地后自动注册）。不改 main.py/celery_app.py。

---

## 3. 生成产物（3 份功能设计文档）
- domain-entities.md：BloggerImportAdapter 契约 + manual_blogger 13 列映射 + _split_tags/int/Decimal 转换 + 单实体映射
- business-rules.md：BR-U06c-01~：标识/字段映射/标签解析/校验矩阵/单次 upsert 幂等/事务边界/框架边界
- business-logic-model.md：UC（注册/端到端导入/行级失败重试/自定义映射/标签解析）+ 端到端样本 CSV

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06c/functional-design/{domain-entities,business-rules,business-logic-model}.md`

---

**等待用户回复"继续"批准本计划（含 10 个 [Answer]），开始生成 3 份功能设计文档。**
