# U06d 业务规则（Business Rules）

> 单元：U06d — 推广导入适配器
> 范围：PromotionImportAdapter 的解析/校验/FK 解析/INSERT 规则 + 与 U06a 框架边界
> 框架级规则（去重/上传/重试/状态机）继承 U06a

---

## 1. 标识与注册（BR-U06d-01~03）

| 规则 | 说明 |
|---|---|
| **BR-U06d-01** | source=`manual_promotion`；target_table=`promotion` |
| **BR-U06d-02** | 模块 `app.modules.importer.adapters.promotion` 提供 `register()`，由 U06a register_import_adapters 双进程加载（main.py 已预置路径） |
| **BR-U06d-03** | upload source 白名单校验 + runner registry.get 二次防御（继承 U06a） |

---

## 2. 字段映射规则（BR-U06d-10~16）

| 规则 | 说明 |
|---|---|
| **BR-U06d-10** | mapping 优先级：field_mapping > 内置默认（domain-entities §4） |
| **BR-U06d-11** | 必填：style_code / xiaohongshu_id / quote_amount / platform / cooperation_date |
| **BR-U06d-12** | 可空：sku_code / cost_snapshot / scheduled_publish_date / note_title / remark |
| **BR-U06d-13** | quote_amount / cost_snapshot：非空时 Decimal（**禁 float**）≥0；quote_amount 必填 |
| **BR-U06d-14** | cooperation_date 必需合法 date（YYYY-MM-DD）；scheduled_publish_date 可选合法 date |
| **BR-U06d-15** | platform 空 → 默认"小红书" |
| **BR-U06d-16** | 长度上限（对齐 U04）：style_code≤64 / sku_code≤64 / xiaohongshu_id≤64 / platform≤16 / note_title≤255 |

---

## 3. 行校验矩阵（validate，BR-U06d-20，纯函数不查 FK）

| 校验项 | error_detail 文案 |
|---|---|
| style_code 必填 | `款式编码不能为空` |
| xiaohongshu_id 必填 | `小红书ID不能为空` |
| quote_amount 必填且 Decimal ≥0 | `报价金额必须为非负数字` |
| platform 必填（空→默认，不报错） | — |
| cooperation_date 必填且合法 date | `合作日期不能为空 / 合作日期格式错误（应为 YYYY-MM-DD）` |
| cost_snapshot 非空时 Decimal ≥0 | `成本快照必须为非负数字` |
| scheduled_publish_date 非空时合法 date | `计划发布日期格式错误（应为 YYYY-MM-DD）` |
| 长度上限 | `<字段> 超过长度上限 N` |

> **FK 存在性不在 validate**（需 DB）；放 upsert 阶段（BR-U06d-31~32）。

---

## 4. FK 解析与 INSERT 规则（BR-U06d-30~37）

| 规则 | 说明 |
|---|---|
| **BR-U06d-30** | 写入语义 = **INSERT-only**（每行建新 promotion；is_inserted 恒 True） |
| **BR-U06d-31** | style_code → style_id（必需）：`StyleRepository.get_by_code`；未找到 → 行失败 `款式编码 X 不存在` |
| **BR-U06d-32** | xiaohongshu_id → blogger_id（必需）：`BloggerRepository.get_by_xiaohongshu_id`；未找到 → 行失败 `博主 X 不存在` |
| **BR-U06d-33** | sku_code → sku_id（可选）：提供时 `SkuRepository.get_by_code`，未找到 → 行失败 `SKU编码 X 不存在`（提供了就必须有效）；不提供 → None |
| **BR-U06d-34** | internal_code = `next_internal_sequence(tenant_id, cooperation_date)`（FB2 原子）+ `format_internal_code(tenant_code, cooperation_date, sequence)`；tenant_code 实例级缓存 |
| **BR-U06d-35** | 快照：style_code_snapshot=style.style_code；style_short_name_snapshot=style.short_name or style.style_name |
| **BR-U06d-36** | 3 状态默认初始态：publish_status=未发布 / recall_status=未召回 / settlement_status=未核查（不从文件导入状态） |
| **BR-U06d-37** | pr_id = actor_id（batch.created_by）；cost_snapshot 从文件或 None（MVP 不自动从 sku 算） |

---

## 5. 事务与租户上下文（BR-U06d-40~42，继承 FB-C/NF-1）

| 规则 | 说明 |
|---|---|
| **BR-U06d-40** | adapter.upsert 不自 commit；runner 持有 per-row 事务 |
| **BR-U06d-41** | FK 解析 + next_internal_sequence + INSERT 同 per-row 事务原子；任一失败整行回滚（含 sequence UPDATE，不浪费号，但并发下可能跳号，可接受） |
| **BR-U06d-42** | runner per-row SET LOCAL（NF-1）+ ORM 钩子注入 tenant_id；FK 查询与 sequence 受 RLS 约束 |

---

## 6. 幂等与已知限制（BR-U06d-50~52）

| 规则 | 说明 |
|---|---|
| **BR-U06d-50** | 同文件 upload → U06a hash 409；同 batch retry only_failed 仅重跑失败行（成功行已建 promotion 不重复） |
| **BR-U06d-51** | **已知限制**：两个不同文件含相同逻辑推广 → 创建重复 promotion（INSERT-only，无导入层 dedup 键；与 U04 重复检测为 warning 一致）。记入文档，V1 评估 |
| **BR-U06d-52** | SequenceOverflowError（当天 >9999）→ 行失败（`当天推广序号已达上限`） |

---

## 7. 错误码与失败处理（BR-U06d-60~61）

| 规则 | 说明 |
|---|---|
| **BR-U06d-60** | 行级失败（校验 / FK 缺失 / 序列溢出 / INSERT 异常）→ import_job.failed + error_detail，不冒泡 HTTP |
| **BR-U06d-61** | upload 层错误（格式/大小/source/去重 409）由 U06a 处理 |

---

## 8. 与 U06a 框架边界（BR-U06d-70）

| U06d 做 | U06d 不做（U06a/U04 提供） |
|---|---|
| PromotionImportAdapter 三方法 + register() | upload/重试/下载/映射端点 + hash 去重 + 状态机 |
| manual_promotion 映射 + FK 解析 + internal_code 生成 + 快照 | run_import_batch runner / promotion 表 / next_internal_sequence 实现 / format_internal_code |
| INSERT promotion（初始态） | promotion 状态推进（U04 API） |

> 不改 runner / U04 schema / 不新增表/端点/Celery 任务/权限/migration。

---

## 9. 验收对齐（unit-of-work U06d）
- ✅ PromotionImportAdapter 注册（source=manual_promotion）
- ✅ 推广字段映射 + FK 解析（style/blogger 必需 + sku 可选）+ internal_code 生成
- ✅ 端到端样本 CSV 跑通（建 promotion 初始态）
- ✅ 缺 style/blogger → 行失败；行级失败 → 下载 + only_failed 重试
- ✅ 依赖 = U04 + U02 + U03 + U06a（不改框架）
