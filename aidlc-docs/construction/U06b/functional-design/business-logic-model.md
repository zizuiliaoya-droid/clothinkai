# U06b 业务逻辑模型（Business Logic Model）

> 单元：U06b — 商品/SKU 导入适配器
> 范围：5 个用例（注册 / 端到端导入 / 行级失败重试 / 自定义映射 / 解析转换）
> 所有用例复用 U06a 框架编排；本单元聚焦 StyleSkuImportAdapter 在 runner per-row 事务内的行为

---

## UC-1 适配器注册（启动期）

**触发**：HTTP 进程 lifespan / Celery worker_process_init（U06a register_import_adapters）

```mermaid
sequenceDiagram
    participant Main as main.py / worker_process_init
    participant RIA as register_import_adapters（U06a）
    participant Mod as adapters.style_sku
    participant Reg as ImportAdapterRegistry

    Main->>RIA: 启动调用
    RIA->>Mod: import_module + getattr("register")
    Note over RIA: ModuleNotFoundError → warning（U06b 未部署时不阻塞）
    Mod->>Reg: register(StyleSkuImportAdapter())
    Reg-->>Reg: _adapters["manual_style_sku"] = adapter
    Note over Reg: upload 时 sources() 含 manual_style_sku → 白名单通过
```

**关键点**：U06a 的 register_import_adapters 已含 `app.modules.importer.adapters.style_sku`；U06b 落地模块 + `register()` 后两进程自动注册（HTTP 用于 upload 白名单校验，worker 用于 runner 取 adapter）。

---

## UC-2 端到端导入（主流程）

**前置**：运营登录（importer.batch:write）；准备款式-SKU 平铺 CSV/XLSX

```mermaid
sequenceDiagram
    actor User as 运营
    participant API as POST /api/imports/upload（U06a）
    participant Svc as ImportService（U06a）
    participant Run as run_import_batch（U06a runner）
    participant Adp as StyleSkuImportAdapter（U06b）
    participant SR as StyleRepository（U02）
    participant KR as SkuRepository（U02）

    User->>API: upload(file, source=manual_style_sku)
    API->>Svc: 校验 source 白名单 + 格式 + 大小 + hash 去重
    Svc->>Svc: DB 先行 INSERT batch(processing) + 写 R2（NF-2）
    Svc-->>User: 202 {batch_id, status: processing}
    Svc->>Run: run_import_batch.delay(batch_id)

    Run->>Run: bypass 读 batch（tenant_id/source/mapping_version）
    Run->>Adp: registry.get("manual_style_sku")
    Run->>Run: 从 R2 取文件 + _parse_rows（CSV/XLSX）
    loop 每行（per-row 事务 + SET LOCAL app.tenant_id，NF-1）
        Run->>Adp: parse_row(row, mapping)
        Run->>Adp: validate(parsed)
        alt 校验失败
            Run->>Run: import_job.failed（独立 bypass session）
        else 通过
            Run->>Adp: upsert(parsed, session, tenant_id, actor_id)
            Adp->>SR: get_by_code(style_code)
            alt style 存在
                SR-->>Adp: 复用 style.id（不更新）
            else 不存在
                Adp->>SR: session.add(Style(...)) + flush
            end
            Adp->>KR: upsert_atomic(tenant_id, sku values) → (sku, is_inserted)
            Adp-->>Run: (sku.id, is_inserted)
            Run->>Run: import_job.success(target_resource_id=sku.id) + commit
        end
    end
    Run->>Run: 汇总 imported/failed → batch.completed/partial/failed
```

**汇总规则**（U06a runner）：failed=0 且 total>0 → completed；imported=0 → failed；否则 partial。

---

## UC-3 行级失败 + 重试（FB-E only_failed）

**场景**：CSV 含一行缺 SKU编码 → 该行 failed，其余成功 → batch=partial

```mermaid
sequenceDiagram
    actor User as 运营
    participant DL as GET /batches/{id}/errors/download（U06a）
    participant RT as POST /batches/{id}/retry（U06a）
    participant Run as run_import_batch（only_failed=True）
    participant Adp as StyleSkuImportAdapter

    User->>DL: 下载失败明细 CSV
    DL-->>User: row_number / error_detail(SKU编码不能为空) / raw_data
    User->>User: 修正源文件后...（或直接重试已有 failed 行）
    User->>RT: retry(batch_id)
    Note over RT: claim_for_retry（NF-3 原子）+ failed>0 → only_failed=True
    RT->>Run: apply_async(only_failed=True, countdown)
    Run->>Run: _load_failed_rows（用 import_job.raw_data 还原失败行）
    loop 仅失败行
        Run->>Adp: parse_row/validate/upsert
        Run->>Run: ON CONFLICT(batch_id,row_number) 原地更新 import_job（attempt_count+1）
    end
    Run->>Run: 重算 import_job 成功/失败总数 → batch 终态
```

> only_failed 路径用 import_job.raw_data 还原原始行；重试是否成功取决于该行数据本身（如 raw_data 本就缺 sku_code，则重试仍失败，需运营改源文件重新 upload 新 batch）。

---

## UC-4 自定义字段映射覆盖

**场景**：某租户的导出文件列名是"商品货号/规格编码"而非默认"款式编码/SKU编码"

```mermaid
sequenceDiagram
    actor Admin as PR主管（importer.mapping:write）
    participant FM as POST /api/imports/field-mappings（U06a）
    participant Svc as FieldMappingService（U06a）

    Admin->>FM: create(source=manual_style_sku, columns=[商品货号→style_code, 规格编码→sku_code, ...])
    FM->>Svc: validate_mapping_config + next_version + 旧 active 下线
    Svc-->>Admin: 201 FieldMapping(version=2, is_active=true)
    Note over Svc: 之后 upload(source=manual_style_sku) 的 batch.mapping_version=2
    Note over Svc: runner 加载 v2 → adapter.parse_row 按 v2 列名映射
```

> 历史 batch 在 import_batch.mapping_version 记录所用版本（可追溯）；adapter parse_row 收到 runner 按版本加载的 mapping；mapping=None（无 active 且 batch.mapping_version=NULL）→ 回退内置默认映射。

---

## UC-5 解析与类型转换（parse_row 内部）

```mermaid
flowchart TD
    ROW["原始行 dict<br/>{款式编码: 'ST001', 成本价: '1,299.00', 货源类型: '采购'}"]
    ROW --> RESOLVE["解析 mapping（v2 或内置默认）"]
    RESOLVE --> MAPCOL["逐列：source_col → target_field"]
    MAPCOL --> CONV{"type 转换"}
    CONV -->|str| S["strip()，空→None"]
    CONV -->|decimal| D["去千分位 + Decimal<br/>'1,299.00'→Decimal('1299.00')"]
    S --> OUT["parsed dict<br/>{style_code:'ST001', cost_price:Decimal('1299.00'), sourcing_type:'采购'}"]
    D --> OUT
    OUT --> NOTE["raw_data 仍存原始行（保真）"]
```

---

## 用例汇总

| UC | 名称 | 复用 U06a | U06b 新增 |
|---|---|---|---|
| UC-1 | 适配器注册 | register_import_adapters / Registry | register() + adapter 类 |
| UC-2 | 端到端导入 | upload / runner / 8 端点 | parse_row / validate / upsert |
| UC-3 | 行级失败重试 | retry / claim / 下载 / only_failed | adapter 行为（缺字段 → failed） |
| UC-4 | 自定义映射 | field-mapping API / 版本管理 | manual_style_sku 列定义 |
| UC-5 | 解析转换 | runner _parse_rows | 类型转换 + 默认映射回退 |

---

## 端到端验收样本（测试 fixture 设计）

样本 CSV（`manual_style_sku` 默认表头）：

| 款式编码 | 款式名称 | 类目 | SKU编码 | 颜色 | 尺码 | 成本价 | 货源类型 | 预期 |
|---|---|---|---|---|---|---|---|---|
| ST-A | 连衣裙A | 连衣裙 | SKA-红-M | 红 | M | 39.90 | 自产 | 新建 style + sku（success） |
| ST-A | 连衣裙A | 连衣裙 | SKA-红-L | 红 | L | 39.90 | 自产 | 复用 style ST-A + 新 sku（success） |
| ST-B | 上衣B | 上衣 |  | 蓝 | M | 20.00 | 采购 | 缺 SKU编码 → failed |

预期 batch：total_rows=3, imported=2, failed=1, status=partial；import_job 第3行 error_detail 含"SKU编码不能为空"。
