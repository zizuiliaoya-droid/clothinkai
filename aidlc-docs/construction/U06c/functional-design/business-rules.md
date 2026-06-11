# U06c 业务规则（Business Rules）

> 单元：U06c — 博主导入适配器
> 范围：BloggerImportAdapter 的解析/校验/upsert 规则 + 与 U06a 框架边界
> 框架级规则（去重/上传/重试/状态机）继承 U06a，本单元仅引用

---

## 1. 适配器标识与注册（BR-U06c-01~03）

| 规则 | 说明 |
|---|---|
| **BR-U06c-01** | source=`manual_blogger`；target_table=`blogger` |
| **BR-U06c-02** | 模块 `app.modules.importer.adapters.blogger` 提供 `register()`，由 U06a register_import_adapters 双进程加载（main.py 已预置路径） |
| **BR-U06c-03** | upload 时 source 白名单校验由 U06a 执行（manual_blogger ∈ registry.sources()）；runner registry.get 二次防御 |

---

## 2. 字段映射规则（BR-U06c-10~17）

| 规则 | 说明 |
|---|---|
| **BR-U06c-10** | mapping 优先级：field_mapping（运营自定义）> 内置默认映射 |
| **BR-U06c-11** | 必填字段：`xiaohongshu_id` / `nickname` |
| **BR-U06c-12** | 可空字段：platform / wechat / phone / follower_count / blogger_type / gender_target / category_tags / quality_tags / quote / cooperation_history / remark |
| **BR-U06c-13** | follower_count：非空时去千分位后 int 且 ≥0；空 → None |
| **BR-U06c-14** | quote：非空时去千分位后 Decimal（**禁 float**）且 ≥0；空 → None |
| **BR-U06c-15** | category_tags / quality_tags：分隔字符串（`;` `；` `,` `，`）→ 拆分 strip 去空 → JSONB 数组；空 → `[]` |
| **BR-U06c-16** | platform 空 → 默认"小红书"（U03 server_default 一致） |
| **BR-U06c-17** | 长度上限（对齐 U03）：xiaohongshu_id≤64 / nickname≤128 / wechat≤64 / phone≤32 / platform≤16 / blogger_type≤16 / gender_target≤16 |

---

## 3. 行校验矩阵（validate，BR-U06c-20）

| 校验项 | error_detail 文案 |
|---|---|
| xiaohongshu_id 必填非空 | `小红书ID不能为空` |
| nickname 必填非空 | `昵称不能为空` |
| follower_count 可解析 int ≥0 | `粉丝数必须为非负整数` |
| quote 可解析 Decimal ≥0 | `报价必须为非负数字` |
| 各字段长度上限 | `<字段> 超过长度上限 N` |

> 校验失败 → runner 写 import_job.failed（error_detail join `; `），per-row 事务隔离。

---

## 4. upsert 规则（BR-U06c-30~33，单实体）

| 规则 | 说明 |
|---|---|
| **BR-U06c-30** | 一行 = 一个 Blogger（单实体，无关联） |
| **BR-U06c-31** | `BloggerRepository.upsert_atomic(tenant_id, values)` → `ON CONFLICT(tenant_id, xiaohongshu_id) WHERE is_deleted=false DO UPDATE`（复用 U03 既有） |
| **BR-U06c-32** | upsert 不更新字段（U03 既有排除）：id / tenant_id / created_at / xiaohongshu_id / is_deleted |
| **BR-U06c-33** | 返回 (blogger.id, is_inserted)；target_resource_id=blogger.id；actor_id 不写业务表（U03 blogger 无 created_by） |

---

## 5. 事务与租户上下文（BR-U06c-40~41，继承 U06a FB-C/NF-1）

| 规则 | 说明 |
|---|---|
| **BR-U06c-40** | adapter.upsert 不自 commit；runner 持有 per-row 事务（成功 commit / 失败 rollback + bypass 写 failed job） |
| **BR-U06c-41** | runner per-row SET LOCAL app.tenant_id（NF-1）+ ORM 钩子注入；upsert_atomic 显式传 tenant_id |

---

## 6. 错误码与失败处理（BR-U06c-50~52）

| 规则 | 说明 |
|---|---|
| **BR-U06c-50** | 行级失败（校验/upsert 异常）→ import_job.failed + error_detail，不冒泡 HTTP |
| **BR-U06c-51** | upload 层错误（格式/大小/source/去重 409）由 U06a 处理 |
| **BR-U06c-52** | 重试继承 U06a FB-E：行级失败 → retry only_failed（原地更新 attempt_count） |

---

## 7. 与 U06a 框架边界（BR-U06c-60）

| U06c 做 | U06c 不做（U06a 提供） |
|---|---|
| BloggerImportAdapter 三方法 + register() | upload/batches/retry/下载/field-mapping 端点 |
| manual_blogger 默认映射 + 标签/int/Decimal 解析 + 校验 | hash 去重 / 状态机 / 重试编排 / CSV 解析 |
| 单次 blogger upsert（复用 U03 upsert_atomic） | run_import_batch runner（per-row 事务/SET LOCAL/汇总） |

> 不改 runner、不新增表/端点/Celery 任务/权限。

---

## 8. 验收对齐（unit-of-work U06c）
- ✅ BloggerImportAdapter 注册（source=manual_blogger）
- ✅ 博主字段映射（默认 + 自定义；含标签 JSONB 数组）
- ✅ 端到端样本 CSV 跑通（upsert blogger）
- ✅ 行级失败 → import_job.failed → 下载 + only_failed 重试
- ✅ 依赖 = U03 + U06a（不改框架）
