# U06c NFR 设计计划（NFR Design Plan）

> 单元：U06c — 博主导入适配器
> 范围：1 个增量模式 P-U06c-01（单实体 upsert + 标签/int/Decimal 解析编排）；其余继承 U06a P-U06a-01~05 + U03 upsert_atomic + U06b P-U06b-01 思路
> 节奏：极小增量（比 U06b 更简单 —— 单实体无两实体编排）

---

## 1. 与基线模式的关系

| 模式 | 来源 | U06c 用法 |
|---|---|---|
| P-U06a-01 Runner 事务 + 租户上下文（NF-1） | U06a | adapter 在 per-row 事务内执行 |
| P-U06a-02 Adapter 协议 + Registry（NF-4） | U06a | BloggerImportAdapter + register() |
| P-U06a-03/04/05 上传/重试/安全 | U06a | 框架处理 |
| P-U06b-01 单行 upsert 编排 | U06b | **简化为单实体**（去掉 style get-or-create + brand） |
| U03 upsert_atomic（ON CONFLICT xiaohongshu_id） | U03 | blogger 单次 upsert |

### 1.1 U06c 唯一增量模式
- **P-U06c-01**：单实体 Blogger upsert + 标签/int/Decimal 多类型解析（含 _split_tags JSONB 数组）

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — adapter 内是否经 U03 service？
[Answer] **否，直接用 BloggerRepository**（与 U06b 同决策：U03 BloggerService 自带 commit/audit/字段权限，与 runner per-row 事务边界 FB-C 冲突，worker 无 HTTP User）。adapter 用 runner 传入 session 构造 `BloggerRepository(session)`。

### Q2 — upsert 实现
[Answer] 单次 `BloggerRepository(session).upsert_atomic(tenant_id, values)`（无 style/brand 前置步骤）。values 含全部映射字段（xiaohongshu_id/nickname/platform/wechat/phone/follower_count/blogger_type/gender_target/category_tags/quality_tags/quote/cooperation_history/remark）。返回 (blogger.id, is_inserted)。

### Q3 — 标签解析落点
[Answer] **parse_row 内** `_split_tags`（纯函数，按 `;；,，` 拆分 + strip + 去空 → list）；空 → []。结果直接作为 JSONB 数组传 upsert_atomic（SQLAlchemy JSONB 列接受 Python list）。

### Q4 — int/Decimal 解析与校验
[Answer] parse_row 尽力转换（_to_int / _to_decimal，禁 float，非法保留原串）；validate 检测非 int/非 Decimal/负数 → 统一错误文案。空 → None。

### Q5 — 默认映射 vs field_mapping
[Answer] 模块级 `_DEFAULT_COLUMNS`（13 列，domain-entities §4）；parse_row：mapping 非 None → mapping_config["columns"]，None → _DEFAULT_COLUMNS。

### Q6 — platform 默认值
[Answer] platform 空 → upsert values 传 "小红书"（与 U03 server_default 一致）；或不传该 key 让 DB server_default 生效。选**显式传 "小红书"**（避免 ON CONFLICT UPDATE 路径把已有值覆盖为空）。

### Q7 — 测试模式
[Answer] 复用 U06a/U06b test_import_runner 模式（monkeypatch session + mock get_object_bytes + committed 清理）。真实 BloggerImportAdapter。断言 blogger 入库 + category_tags JSONB 数组 + follower int + quote Decimal + 同 ID UPDATE + tenant_id。

---

## 3. 生成产物（2 份文档）

### 3.1 nfr-design-patterns.md
- **P-U06c-01：单实体 Blogger upsert + 多类型解析**
  - parse_row（_split_tags / _to_int / _to_decimal）+ validate + upsert（单次 upsert_atomic）伪代码
  - 事务契约：复用 runner session 不 commit
  - platform 默认 "小红书"（显式传，防 UPDATE 覆盖）
  - 内置默认 vs field_mapping 双路
- 继承声明：P-U06a-01~05 + P-U06b-01 + U03 upsert_atomic
- 一致性校验

### 3.2 logical-components.md
- 新增组件 adapters/blogger.py（BloggerImportAdapter + _DEFAULT_COLUMNS + _split_tags + _to_int + _to_decimal + register()）
- 复用 U03 BloggerRepository + U06a 框架
- 注册序列（main.py 已预置，无改动）+ 依赖图 + 数据流 + 测试组件
- 无新表/端点/Celery 任务/main.py 改动

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06c/nfr-design/{nfr-design-patterns,logical-components}.md`

---

**等待用户回复"继续"批准本计划（含 7 个 [Answer]），开始生成 2 份 NFR 设计文档。**
