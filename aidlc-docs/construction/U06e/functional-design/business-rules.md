# U06e 业务规则（Business Rules）

> 单元：U06e — 结算导入适配器
> 范围：SettlementImportAdapter 的解析/校验/FK 派生/INSERT 规则 + 与 U06a 框架边界
> 语义：历史结算数据迁移（非日常运营）；框架级规则继承 U06a

---

## 1. 标识与注册（BR-U06e-01~03）

| 规则 | 说明 |
|---|---|
| **BR-U06e-01** | source=`manual_settlement`；target_table=`settlement` |
| **BR-U06e-02** | 模块 `app.modules.importer.adapters.settlement` 提供 `register()`，由 U06a register_import_adapters 双进程加载（main.py 已预置路径） |
| **BR-U06e-03** | upload source 白名单 + runner registry.get 二次防御（继承 U06a） |

---

## 2. 导入语义（BR-U06e-10~12，关键）

| 规则 | 说明 |
|---|---|
| **BR-U06e-10** | U06e = **历史结算数据迁移工具**（INSERT-only），非日常运营；日常结算仍由 U04 SettlementRequested 事件链路创建 |
| **BR-U06e-11** | **不触发任何事件**（不调 event_bus.dispatch）；导入是数据迁移非业务动作，避免重复触发 SettlementPaid 等 |
| **BR-U06e-12** | **不经 U05 SettlementService**（Service 自带 commit/audit/事件/状态机校验，与 runner per-row 事务 FB-C 冲突）→ 直接用 SettlementRepository + PromotionRepository |

---

## 3. 字段映射规则（BR-U06e-20~26）

| 规则 | 说明 |
|---|---|
| **BR-U06e-20** | mapping 优先级：field_mapping > 内置默认（domain-entities §4） |
| **BR-U06e-21** | 必填：promotion_internal_code / settlement_date / amount / total_amount |
| **BR-U06e-22** | 可空：payment_amount / payment_date / settlement_status / note_title / remark |
| **BR-U06e-23** | amount/total_amount/payment_amount：非空时 Decimal（**禁 float**）≥0；amount/total_amount 必填 |
| **BR-U06e-24** | settlement_date 必填合法 date；payment_date 可选合法 date |
| **BR-U06e-25** | settlement_status 非空时 ∈ {待核查, 待付款, 待财务付款, 已付款, 已驳回}；空 → 默认"待核查" |
| **BR-U06e-26** | total_amount 为历史值**不重算**（区别 U05 service 的 amount+SUM(extra_items)）；导入不创建 extra_item |

---

## 4. 行校验矩阵（validate，BR-U06e-30，纯函数不查 FK）

| 校验项 | error_detail 文案 |
|---|---|
| promotion_internal_code 必填 | `推广编号不能为空` |
| settlement_date 必填合法 date | `结算日期不能为空 / 结算日期格式错误（应为 YYYY-MM-DD）` |
| amount 必填 Decimal ≥0 | `金额不能为空 / 金额必须为非负数字` |
| total_amount 必填 Decimal ≥0 | `总金额不能为空 / 总金额必须为非负数字` |
| payment_amount 非空时 Decimal ≥0 | `付款金额必须为非负数字` |
| payment_date 非空时合法 date | `付款日期格式错误（应为 YYYY-MM-DD）` |
| settlement_status 非空时 ∈ 5 枚举 | `结算状态必须为 待核查/待付款/待财务付款/已付款/已驳回 之一` |
| note_title 长度 ≤255 | `note_title 超过长度上限 255` |

> **不强制 per-status 字段完整性**（历史数据可信，区别 live 状态机；如已付款不强制 payment_date 非空）。

---

## 5. FK 派生与 INSERT 规则（BR-U06e-40~46）

| 规则 | 说明 |
|---|---|
| **BR-U06e-40** | 写入语义 = INSERT-only（每行建新 settlement；is_inserted 恒 True） |
| **BR-U06e-41** | promotion_internal_code → promotion（`PromotionRepository.get_by_internal_code`）；未找到 → 行失败 `推广编号 X 不存在` |
| **BR-U06e-42** | **blogger_id / style_id / pr_id 从 promotion 派生**（不让文件提供，保证与 promotion 一致） |
| **BR-U06e-43** | settlement_no = `next_settlement_sequence(tenant_id, settlement_date)` + `format_settlement_no(tenant_code, settlement_date, sequence)`；tenant_code 实例级缓存；SequenceOverflowError → 行失败 |
| **BR-U06e-44** | **request_event_id = uuid4()**（合成；导入无真实 SettlementRequested 事件，满足 UNIQUE 约束 + 标识导入来源） |
| **BR-U06e-45** | **UNIQUE(tenant_id, promotion_id) 冲突**（该 promotion 已有 settlement）→ INSERT flush IntegrityError → catch 转 RowValidationError `该推广已有结算单（不可重复，FB3）`（per-row 事务隔离） |
| **BR-U06e-46** | amount/total_amount 从文件（历史值）；payment_amount/payment_date/settlement_status 从文件（可选）；payment_proof_attachment_id 留 None（附件迁移 V1） |

---

## 6. 事务与租户上下文（BR-U06e-50~52，继承 FB-C/NF-1）

| 规则 | 说明 |
|---|---|
| **BR-U06e-50** | adapter.upsert 不自 commit；runner 持有 per-row 事务 |
| **BR-U06e-51** | promotion 查 + sequence + INSERT 同 per-row 事务原子；冲突/缺失整行回滚 |
| **BR-U06e-52** | runner per-row SET LOCAL（NF-1）+ ORM 钩子注入 tenant_id；查询/INSERT 受 RLS 约束 |

---

## 7. 错误码与失败处理（BR-U06e-60~61）

| 规则 | 说明 |
|---|---|
| **BR-U06e-60** | 行级失败（校验 / promotion 缺失 / UNIQUE 冲突 / 序列溢出）→ import_job.failed + error_detail，不冒泡 HTTP |
| **BR-U06e-61** | upload 层错误（格式/大小/source/文件去重 409）由 U06a 处理 |

---

## 8. 与 U06a / U05 框架边界（BR-U06e-70）

| U06e 做 | U06e 不做（U06a/U05 提供） |
|---|---|
| SettlementImportAdapter 三方法 + register() | upload/重试/下载/映射端点 + hash 去重 + 状态机 |
| manual_settlement 映射 + promotion 派生 + settlement_no + 合成 event_id | run_import_batch runner / settlement 表 / next_settlement_sequence / format_settlement_no |
| INSERT settlement（历史迁移） | settlement 状态推进 / extra_item / 事件触发（U05 service/API） |

> 不改 runner / U05 schema / 不新增表/端点/Celery 任务/权限/migration；**不触发事件 / 不经 U05 Service**。

---

## 9. 验收对齐（unit-of-work U06e）
- ✅ SettlementImportAdapter 注册（source=manual_settlement）
- ✅ 结算字段映射（历史迁移）+ promotion FK 派生 + settlement_no 生成 + 合成 event_id
- ✅ 端到端样本 CSV 跑通（建历史 settlement）
- ✅ 重复 promotion → 行失败（UNIQUE 一对一，FB3）；缺 promotion → 行失败
- ✅ 依赖 = U05 + U04 + U06a（不改框架，不触发事件）
