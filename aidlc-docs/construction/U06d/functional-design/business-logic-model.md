# U06d 业务逻辑模型（Business Logic Model）

> 单元：U06d — 推广导入适配器
> 范围：5 UC（注册 / 端到端导入含 FK 解析+序列+建 promotion / 行级失败 / 自定义映射 / 幂等语义）
> 聚焦 PromotionImportAdapter 在 runner per-row 事务内的 INSERT-only 编排

---

## UC-1 适配器注册（启动期）

复用 U06a register_import_adapters：
```
register_import_adapters() → import_module("app.modules.importer.adapters.promotion")
  → promotion.register() → ImportAdapterRegistry.register(PromotionImportAdapter())
```
> main.py 已预置 `adapters.promotion` 路径；落地后双进程自动注册（NF-4）。

---

## UC-2 端到端导入（主流程，INSERT-only + FK 解析）

```mermaid
sequenceDiagram
    actor User as PR
    participant API as POST /api/imports/upload（U06a）
    participant Run as run_import_batch（U06a runner）
    participant Adp as PromotionImportAdapter（U06d）
    participant SR as StyleRepository（U02）
    participant BR as BloggerRepository（U03）
    participant PR as PromotionRepository（U04）

    User->>API: upload(file, source=manual_promotion)
    API->>API: 校验 + hash 去重 + DB 先行建 batch + 写 R2
    API-->>User: 202 {batch_id, status: processing}
    API->>Run: run_import_batch.delay(batch_id)
    Run->>Adp: registry.get("manual_promotion")
    Run->>Run: R2 取文件 + _parse_rows
    loop 每行（per-row 事务 + SET LOCAL，NF-1）
        Run->>Adp: parse_row（_to_decimal/_to_date）
        Run->>Adp: validate（必填/数值/date，不查 FK）
        alt 校验失败
            Run->>Run: import_job.failed（bypass）
        else 通过
            Run->>Adp: upsert(parsed, session, tenant_id, actor_id)
            Adp->>SR: get_by_code(style_code)
            alt style 不存在
                Adp-->>Run: raise → failed（款式编码不存在）
            end
            Adp->>BR: get_by_xiaohongshu_id(xhs_id)
            alt blogger 不存在
                Adp-->>Run: raise → failed（博主不存在）
            end
            Adp->>PR: next_internal_sequence(tenant_id, cooperation_date)
            Adp->>Adp: format_internal_code(tenant_code, date, seq)
            Adp->>PR: add(Promotion(初始态 + 快照 + pr_id=actor)) + flush
            Adp-->>Run: (promotion.id, True)
            Run->>Run: import_job.success + commit
        end
    end
    Run->>Run: 汇总 → batch.completed/partial/failed
```

---

## UC-3 行级失败（FK 缺失 / 序列溢出）

| 失败类型 | error_detail |
|---|---|
| style_code 不存在 | `款式编码 ST999 不存在` |
| xiaohongshu_id 不存在 | `博主 xhs999 不存在` |
| sku_code 提供但不存在 | `SKU编码 SK999 不存在` |
| 当天序号 >9999 | `当天推广序号已达上限` |
| 必填缺失 / 数值非法 / date 非法 | validate 文案 |

> 行失败 → import_job.failed（含 raw_data 供下载/重试）；per-row 事务隔离。retry only_failed：补齐 style/blogger 后**需重新 upload 新文件**（FK 数据修复在主数据侧；原 batch retry 仅重跑同 raw_data，若 style/blogger 仍缺则仍失败）。

---

## UC-4 自定义字段映射

运营导出文件列名不同 → U06a `POST /api/imports/field-mappings`（source=manual_promotion）建 active 版本 → batch.mapping_version 记录 → runner 加载 → parse_row 按自定义列名。

---

## UC-5 幂等语义

```mermaid
flowchart TD
    A["同文件 upload"] -->|U06a hash| A1["409（框架层）"]
    B["同 batch retry only_failed"] -->|仅失败行| B1["成功行已建 promotion，不重复"]
    C["两个不同文件含相同推广"] -->|INSERT-only 无 dedup 键| C1["创建重复 promotion（已知限制，V1 评估）"]
```

> internal_code 系统生成 → 每次 INSERT 都是新 promotion；幂等仅靠 U06a 文件 hash + batch 内 UNIQUE(batch_id,row_number)，跨文件不去重（与 U04 重复检测为 warning 一致）。

---

## 用例汇总

| UC | 名称 | 复用 | U06d 新增 |
|---|---|---|---|
| UC-1 | 注册 | U06a register | register() |
| UC-2 | 端到端导入 | U06a runner + U02/U03/U04 repo | parse_row/validate/upsert（FK 解析 + 序列 + INSERT） |
| UC-3 | 行级失败 | U06a retry/下载 | FK 缺失/序列溢出错误 |
| UC-4 | 自定义映射 | U06a field-mapping | manual_promotion 列 |
| UC-5 | 幂等语义 | U06a hash + UNIQUE(batch,row) | INSERT-only 限制说明 |

---

## 端到端验收样本（测试 fixture 设计）

前置：测试 seed style(ST-A) + blogger(xhs-A)。

| 款式编码 | 小红书ID | 报价金额 | 平台 | 合作日期 | 预期 |
|---|---|---|---|---|---|
| ST-A | xhs-A | 500.00 | 小红书 | 2026-06-01 | 建 promotion（success，internal_code 生成，初始态） |
| ST-A | xhs-A | 600 | 小红书 | 2026-06-01 | 再建一个（INSERT-only，不同 internal_code seq+1） |
| ST-999 | xhs-A | 100 | 小红书 | 2026-06-01 | 款式不存在 → failed |
| ST-A | （空） | 100 | 小红书 | 2026-06-01 | 缺小红书ID → failed |

预期 batch：total_rows=4, imported=2, failed=2, status=partial；两条成功 promotion 的 internal_code 序号连续（同 cooperation_date）。
