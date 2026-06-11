# U06a 非功能需求（NFR Requirements）

> 单元：U06a — 统一导入框架  
> 范围：U06a 特异性 NFR 增量（异步导入 / 文件解析 / R2 中转 / Celery 重试 / 行级编排）；通用 NFR 全部继承 U01-U05

---

## 1. 与 U01-U05 NFR 基线的关系

### 1.1 完全继承
- 错误码体系 / 认证 / 授权 / 多租户 RLS 双引擎（U01）
- 监控（Prometheus + Sentry + structlog）/ 健康检查 / pytest 框架（U01）
- Celery 基线（celery-worker + celery-beat + default/backup 队列 + autoretry 模式）（U01）
- R2 helper（`upload_bytes` / `get_signed_url` / boto3 client.get_object / `delete`）（U01）— **不依赖 U05 Attachment ORM**（FB-A）
- 审计敏感值脱敏 + 独立 bypass session 写失败记录（U05 模式）

### 1.2 U06a 增量
- **异步导入吞吐**：5 万行/batch SLA
- **文件解析内存**：流式 O(1)
- **upload 同步段时延**：hash + R2 写 + 建 batch
- **Celery 失败语义**：任务级不 autoretry + 行级失败计入 import_job + 解析致命失败 → batch.failed
- **行级幂等**：重跑不重复（adapter 业务键 + UNIQUE(batch_id,row_number)）
- **worker 租户上下文**：SET app.tenant_id 会话级跨 per-row commit（RLS 正确性）
- **文件上传安全**：白名单 + 上限 + CSV injection 防护
- **5 个自定义 Prometheus 指标**

---

## 2. 性能 NFR

### 2.1 SLA

| 路径 | 指标 | 目标 | 备注 |
|---|---|---|---|
| upload 同步段 | P95 | ≤ 2s | SHA256 流式 + R2 写（20MB 文件 R2 写为主）+ 建 batch + enqueue |
| run_import_batch（5 万行简单 adapter） | 完成时间 | ≤ 5 分钟 | 行级独立事务 ~150-200 行/秒；异步，不阻塞用户 |
| run_import_batch（冒烟 1000 行） | 完成时间 | < 30s | CI/本地冒烟阈值 |
| 失败明细下载（1 万 failed 行） | P95 | ≤ 3s | StreamingResponse 流式生成 |
| batch 列表 / 详情 | P95 | ≤ 200ms | 走 idx_import_batch_tenant_status |

### 2.2 内存
- CSV：标准库 `csv.reader` 逐行迭代，内存 O(1) 行缓冲
- XLSX：`openpyxl.load_workbook(read_only=True)`，按行 `iter_rows`，不全量解压到内存
- SHA256：上传时分块流式读取（如 8KB chunk），不全量载入

### 2.3 容量

| 对象 | MVP 预估 | 增长 |
|---|---|---|
| import_batch / 租户 | ≤ 数千（每日数十次导入） | 线性，列表分页 |
| import_job / batch | ≤ 5 万（单文件行数上限） | `UNIQUE(batch_id, row_number)`；10 万级分区留 V1 |
| import_job 总量 / 租户 | 批次数 × 平均行数 | V1 评估归档（如完成 90 天后清 success 行，保留 batch 汇总） |
| field_mapping / (租户,source) | 个位数版本 | 永久保留（不删） |
| R2 imports/ 文件 | = batch 数 | MVP 不清理；V1 评估保留期 |

---

## 3. 可靠性 NFR

### 3.1 Celery 失败语义（FB-E）

| 失败类型 | 处理 | 重试 |
|---|---|---|
| 基础设施异常（DB 连接断 / R2 不可达） | Celery autoretry 1 次（短退避） | 任务级 autoretry=1 |
| 解析致命失败（文件损坏 / 格式错 / 超行数） | batch.status=failed + error_summary，**不 autoretry** | 用户端点级手动 retry（重跑整文件） |
| 行级失败（validate / upsert 异常） | 写 import_job.failed，**不中断 batch**，继续下一行 | 用户端点级 retry（only_failed） |
| Adapter 未注册 | batch.status=failed + error_summary="adapter_not_registered" | 部署 Adapter 后手动 retry |

### 3.2 行级隔离（FB-C）
- 每行独立事务（per-row BEGIN/COMMIT）：一行失败 ROLLBACK 该行，不影响已成功行
- 失败行用**独立 bypass session** 写 import_job（防被该行回滚带走，复用 U05 `_log_event_dispatch_failure` 模式）
- partial batch：成功行已 commit 入库，失败行未入库；**不整批回滚**（与 services.md §2.5 一致）

### 3.3 重跑幂等（FB-E）
- adapter.upsert 按业务键幂等（U06b-e 保证，如 style_code / xiaohongshu_id / internal_code / settlement_no）
- `UNIQUE(batch_id, row_number)`：重跑 only_failed 时原地更新 import_job（attempt_count += 1），不产生重复 job 行
- 同 batch 重跑安全：已成功行不重处理（only_failed 仅扫 status='failed'）

### 3.4 worker 租户上下文正确性（FB-C）
- run_import_batch 用 engine_app 连接 + `SET app.tenant_id='<tid>'`（**会话级，非 SET LOCAL**），保证跨多个 per-row commit RLS 持续生效
- bypass session 仅用于读 batch 元数据 + 写失败 import_job（系统级，绕 RLS）
- **NFR 测试要求**：跨租户行（adapter 试图写非 batch.tenant_id 的数据）被 RLS / ORM 钩子拦截

---

## 4. 安全 NFR

### 4.1 文件上传威胁模型

| 威胁 | 防护 |
|---|---|
| 恶意文件类型 | 扩展名白名单（.csv/.xlsx）+ MIME 双校验 |
| 超大文件 DoS | `IMPORT_MAX_FILE_MB=20` 上限 → 422 |
| 超多行 DoS | `IMPORT_MAX_ROWS=50000` 上限 → 422 |
| XLSX 宏 / 公式执行 | openpyxl `read_only=True` 不执行宏；只读单元格值 |
| **CSV injection**（失败明细下载） | 导出 CSV 时对以 `=` `+` `-` `@` 开头的字段值加前缀 `'`，防 Excel 公式执行 |
| 路径穿越 | R2 key 用 `imports/{tenant_id}/{batch_id}/` 隔离；safe_filename 去除路径分隔符 |
| 跨租户数据注入 | worker SET app.tenant_id + RLS；adapter 收显式 tenant_id |

### 4.2 权限
- `import:write`（upload / retry / 创建 mapping）/ `import:read`（list / get / 下载）
- 同租户内不限本人（运营协作）；created_by 记录上传者
- U09 后切字段级权限体系

### 4.3 审计
- upload / retry / mapping 创建 / batch 完成写 audit_log，**脱敏**（不记文件内容 / 敏感字段值，仅记 batch_id / source / 计数）

---

## 5. 可观测性 NFR

### 5.1 Prometheus 指标（5 个新增，core/metrics.py 扩展）

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `import_batch_total` | Counter | source, status | batch 完成计数（按来源 + 终态） |
| `import_rows_total` | Counter | source, result | 行级结果计数（result=success/failed） |
| `import_batch_duration_seconds` | Histogram | source | run_import_batch 端到端耗时 |
| `import_file_size_bytes` | Histogram | source | 上传文件大小分布 |
| `import_retry_total` | Counter | source | 重试触发计数 |

### 5.2 日志 / 追踪
- structlog 记 batch_id / source / tenant_id / imported / failed（**不记文件内容**）
- Sentry 捕获：解析致命失败 / adapter 缺失 / R2 不可达
- 告警阈值（V1 Grafana）：`import_rows_total{result="failed"}` 比率突增 / `import_batch_total{status="failed"}` 突增

---

## 6. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | ImportBatchStatus 状态机 / FieldMappingService 版本切换 / FakeImportAdapter / CSV injection 转义 / hash 计算 |
| 集成 | upload（去重 409 / 格式 422 / source 白名单）/ run_import_batch（解析→行级→汇总，用 FakeImportAdapter）/ retry 两类分流 / 失败下载 / **跨租户 RLS 隔离** |
| API | 鉴权 / OpenAPI / DELETE 不提供 |
| 异步 | **同步调用 run_import_batch**（不经 broker）；Celery eager 模式（CELERY_TASK_ALWAYS_EAGER）备选 |
| 覆盖率 | service ≥ 80% / domain ≥ 90% / api ≥ 60%（继承 U01 基线） |

> FakeImportAdapter：内存 upsert + 可配置"第 N 行失败" + 可配置幂等，验证框架编排（解析/分发/行级结果/两类重试/下载/租户上下文）。真实 Adapter 端到端在 U06b/c/d/e 测。

---

## 7. 故事 NFR 映射

| 故事 | NFR 验收 |
|---|---|
| EP07-S07 上传 | upload P95 ≤ 2s；异步解析 5 万行 ≤ 5 分钟；processing 立即返回 |
| EP07-S08 hash 去重 | SHA256 流式；UNIQUE(tenant,source,hash) 409 |
| EP07-S09 映射版本 | 版本切换原子（旧 active 下线 + 新建）；历史 batch 快照 version |
| EP07-S10 失败下载/重试 | 下载 1 万行 ≤ 3s + CSV injection 防护；retry only_failed 幂等 + 退避 1s/5s/30s |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 性能 SLA 量化（upload / 解析 / 下载 / 列表） | ✅ §2.1 |
| Celery 失败语义明确（4 类，FB-E） | ✅ §3.1 |
| 行级隔离 + bypass 兜底（FB-C） | ✅ §3.2 |
| 重跑幂等（adapter + UNIQUE(batch_id,row_number)） | ✅ §3.3 |
| worker 租户上下文 RLS 正确性（FB-C） | ✅ §3.4 + §6 跨租户测试 |
| 文件威胁模型 + CSV injection | ✅ §4.1 |
| 5 个 Prometheus 指标 | ✅ §5.1 |
| 不依赖 U05 Attachment ORM（FB-A） | ✅ §1.1 用 U01 R2 helper |
