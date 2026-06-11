# U06e 业务逻辑模型（Business Logic Model）

> 单元：U06e — 结算导入适配器（历史结算数据迁移）
> 范围：5 UC（注册 / 端到端历史迁移 / 重复 promotion 失败 / 自定义映射 / 状态导入）
> 聚焦 SettlementImportAdapter 在 runner per-row 事务内的 INSERT-only + promotion 派生编排

---

## UC-1 适配器注册（启动期）

复用 U06a register_import_adapters：
```
register_import_adapters() → import_module("app.modules.importer.adapters.settlement")
  → settlement.register() → ImportAdapterRegistry.register(SettlementImportAdapter())
```
> main.py 已预置 `adapters.settlement` 路径；落地后双进程自动注册（NF-4）。

---

## UC-2 端到端历史迁移（主流程，INSERT-only + promotion 派生）

```mermaid
sequenceDiagram
    actor Admin as 运维/管理员
    participant API as POST /api/imports/upload（U06a）
    participant Run as run_import_batch（U06a runner）
    participant Adp as SettlementImportAdapter（U06e）
    participant PR as PromotionRepository（U04）
    participant SR as SettlementRepository（U05）

    Admin->>API: upload(file, source=manual_settlement)
    API->>API: 校验 + hash 去重 + DB 先行建 batch + 写 R2
    API-->>Admin: 202 {batch_id, status: processing}
    API->>Run: run_import_batch.delay(batch_id)
    Run->>Adp: registry.get("manual_settlement")
    Run->>Run: R2 取文件 + _parse_rows
    loop 每行（per-row 事务 + SET LOCAL，NF-1）
        Run->>Adp: parse_row（_to_date/_to_decimal）
        Run->>Adp: validate（必填/数值/date/status，不查 FK）
        alt 校验失败
            Run->>Run: import_job.failed（bypass）
        else 通过
            Run->>Adp: upsert(parsed, session, tenant_id, actor_id)
            Adp->>PR: get_by_internal_code(promotion_internal_code)
            alt promotion 不存在
                Adp-->>Run: raise → failed（推广编号不存在）
            end
            Note over Adp: blogger_id/style_id/pr_id 从 promotion 派生
            Adp->>SR: next_settlement_sequence(tenant_id, settlement_date)
            Adp->>Adp: format_settlement_no + request_event_id=uuid4()
            Adp->>SR: add(Settlement(派生 + 状态 + 合成 event_id)) + flush
            alt UNIQUE(promotion_id) 冲突
                Adp-->>Run: IntegrityError → RowValidationError（该推广已有结算单）→ failed
            else 成功
                Adp-->>Run: (settlement.id, True)
                Run->>Run: import_job.success + commit
            end
        end
    end
    Run->>Run: 汇总 → batch.completed/partial/failed
```

> **不触发任何事件**（与 U05 service mark_paid 发 SettlementPaid 不同）。

---

## UC-3 重复 promotion 失败（UNIQUE 一对一，FB3）

```mermaid
flowchart TD
    A["导入行：推广编号 P1"] --> B{"P1 已有 settlement?<br/>（事件创建 or 之前导入）"}
    B -->|有| C["INSERT flush → IntegrityError<br/>→ RowValidationError(该推广已有结算单)<br/>→ import_job.failed"]
    B -->|无| D["INSERT 成功 → success"]
```

> FB3：财务记录永久不可替换 → 导入绝不覆盖既有 settlement（无论来源）。

---

## UC-4 自定义字段映射

运维遗留文件列名不同 → U06a `POST /api/imports/field-mappings`（source=manual_settlement）建 active 版本 → batch.mapping_version → runner 加载 → parse_row 按自定义列名。

---

## UC-5 历史状态导入

```mermaid
flowchart LR
    ROW["结算状态=已付款<br/>付款金额=530 付款日期=2026-05-10"]
    ROW --> VAL["validate：status ∈ 5 枚举<br/>（不强制 per-status 字段完整性）"]
    VAL --> INS["INSERT settlement<br/>settlement_status=已付款<br/>payment_amount/payment_date 从文件<br/>（不触发 SettlementPaid 事件）"]
```

> 历史数据可信：导入终态结算（如已付款）允许，payment 字段从文件可选导入；区别 live 状态机的逐步推进 + 强校验。

---

## 用例汇总

| UC | 名称 | 复用 | U06e 新增 |
|---|---|---|---|
| UC-1 | 注册 | U06a register | register() |
| UC-2 | 端到端历史迁移 | U06a runner + U04/U05 repo | parse_row/validate/upsert（promotion 派生 + settlement_no + 合成 event_id） |
| UC-3 | 重复 promotion 失败 | U06a per-row 隔离 | UNIQUE(promotion_id) IntegrityError catch |
| UC-4 | 自定义映射 | U06a field-mapping | manual_settlement 列 |
| UC-5 | 历史状态导入 | — | settlement_status 枚举校验 + 不触发事件 |

---

## 端到端验收样本（测试 fixture 设计）

前置：测试 seed promotion(P1, internal_code=XY...0001) + 关联 blogger/style；P2 已有 settlement（模拟事件创建）。

| 推广编号 | 结算日期 | 金额 | 总金额 | 结算状态 | 预期 |
|---|---|---|---|---|---|
| XY...0001 | 2026-06-01 | 500 | 530 | 待核查 | 建 settlement（success，settlement_no 生成，派生 blogger/style/pr） |
| XY...0002（已有 settlement） | 2026-06-01 | 500 | 500 | 已付款 | UNIQUE(promotion_id) 冲突 → failed |
| XY...9999（不存在） | 2026-06-01 | 100 | 100 | 待核查 | 推广编号不存在 → failed |

预期 batch：total_rows=3, imported=1, failed=2, status=partial；成功 settlement 的 blogger_id/style_id == promotion 的。
