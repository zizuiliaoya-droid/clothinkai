# U06a 技术栈决策（Tech Stack Decisions）

> 单元：U06a — 统一导入框架  
> 范围：U06a 新增/复用技术选型；通用栈继承 U01-U05

---

## 1. 选型一览

| 关注点 | 选型 | 来源 | 说明 |
|---|---|---|---|
| CSV 解析 | 标准库 `csv` | Python 内置 | 逐行流式 reader，无新依赖 |
| XLSX 解析 | `openpyxl==3.1.5` | **新增** | `load_workbook(read_only=True)` 流式 iter_rows，不执行宏 |
| 文件上传接收 | `python-multipart`（已装） | U01 | FastAPI UploadFile multipart |
| 对象存储 | `boto3`（已装）via U01 AttachmentService helper | U01 | `upload_bytes` / client.get_object / `delete`（**不经 Attachment ORM**，FB-A） |
| 异步任务 | `celery`（已装） | U01 | 复用 default 队列；`run_import_batch` 任务 |
| 哈希 | 标准库 `hashlib.sha256` | Python 内置 | 分块流式计算 |
| 指标 | `prometheus-fastapi-instrumentator`（已装）+ 自定义 Counter/Histogram | U01 | core/metrics.py 扩展 5 个指标 |
| 测试 | pytest（已装）+ FakeImportAdapter + Celery eager | U01 | 同步调用任务函数 |

> **唯一新增依赖**：`openpyxl==3.1.5`。

---

## 2. requirements.txt 增量

```diff
# 文件解析（U06a 导入框架）
+ openpyxl==3.1.5
```

> CSV / hashlib / multipart 均无需新增（标准库或已装）。

---

## 3. 新增配置（core/config.py）

```python
# ------------------------------------------------------------------ #
# 导入框架（U06a）
# ------------------------------------------------------------------ #
IMPORT_MAX_FILE_MB: int = 20            # 上传文件大小上限
IMPORT_MAX_ROWS: int = 50000            # 单文件数据行数上限（不含表头）
IMPORT_RETENTION_DAYS: int = 0          # 0 = MVP 不清理；V1 设保留期 + Celery beat 清理
IMPORT_BUCKET: str = "private"          # 导入文件 R2 桶（固定 private）
```

环境变量（可选覆盖）：`IMPORT_MAX_FILE_MB` / `IMPORT_MAX_ROWS` / `IMPORT_RETENTION_DAYS`。

---

## 4. Celery 任务配置

```python
# tasks/import_tasks.py
@celery_app.task(
    bind=True,
    name="import.run_import_batch",
    queue="default",              # 复用 U01 default 队列（FB Q4：不新建队列）
    autoretry_for=(OperationalError, EndpointConnectionError),  # 仅基础设施异常
    max_retries=1,                # 解析/行级失败不靠 autoretry（FB-E）
    retry_backoff=5,
)
def run_import_batch(self, batch_id: str, only_failed: bool = False) -> dict: ...
```

- **任务级 autoretry 仅针对基础设施异常**（DB 连接断 / R2 不可达），max_retries=1
- 解析致命失败 / 行级失败**不 autoretry**（写 batch.failed / import_job.failed，由端点级 retry 处理，FB-E）
- 退避（端点级 retry）：Celery `apply_async(countdown=...)` 按 retry_count 取 1s/5s/30s

测试模式：
```python
# 测试 conftest 或 settings
CELERY_TASK_ALWAYS_EAGER = True   # 备选；首选直接 await 任务内部 async 函数
```

---

## 5. R2 访问（复用 U01 AttachmentService，FB-A）

```python
from app.core.attachment import attachment_service

# 上传（upload 同步段）
attachment_service.upload_bytes(file_bytes, bucket="private", key=r2_key, content_type=...)

# 读取（run_import_batch 解析）—— 用底层 client（流式 get_object）
client = attachment_service._client  # 或新增 get_object_stream helper
obj = client.get_object(Bucket=bucket_name, Key=r2_key)
# obj["Body"] 流式读 → csv / openpyxl

# 清理（V1）
attachment_service.delete("private", r2_key)
```

> **不使用 Attachment ORM / 通用 attachment API / ALLOWED_PURPOSES**（FB-A/FB-B）。
> 实施时如需要，给 AttachmentService 加一个 `get_object_bytes(bucket, key)` / `get_object_stream` 薄封装（U06a code generation 阶段评估，属 U01 helper 的合理扩展，不引入 U05 依赖）。

---

## 6. 自定义指标（core/metrics.py 扩展）

```python
from prometheus_client import Counter, Histogram

import_batch_total = Counter(
    "import_batch_total", "导入批次完成计数", ["source", "status"]
)
import_rows_total = Counter(
    "import_rows_total", "导入行级结果计数", ["source", "result"]  # result=success/failed
)
import_batch_duration_seconds = Histogram(
    "import_batch_duration_seconds", "run_import_batch 端到端耗时", ["source"]
)
import_file_size_bytes = Histogram(
    "import_file_size_bytes", "上传文件大小", ["source"],
    buckets=(1e3, 1e4, 1e5, 1e6, 5e6, 1e7, 2e7),
)
import_retry_total = Counter(
    "import_retry_total", "导入重试触发计数", ["source"]
)
```

---

## 7. 解析实现要点

### 7.1 CSV
```python
import csv, io
text = obj_body.read().decode("utf-8-sig")  # 处理 BOM
reader = csv.DictReader(io.StringIO(text))  # 首行表头
for i, row in enumerate(reader, start=1):
    ...  # row: dict[列名, 值]
```
> 大文件可改 `io.TextIOWrapper(obj["Body"])` 流式；MVP ≤ 5 万行内存可控。

### 7.2 XLSX
```python
from openpyxl import load_workbook
wb = load_workbook(io.BytesIO(obj_body), read_only=True, data_only=True)
ws = wb.active
rows = ws.iter_rows(values_only=True)
header = next(rows)
for i, values in enumerate(rows, start=1):
    row = dict(zip(header, values))
    ...
wb.close()
```
- `read_only=True`：流式，不全量载入；`data_only=True`：读公式计算值不读公式串（防注入 + 不执行宏）

### 7.3 SHA256（upload 同步段）
```python
import hashlib
h = hashlib.sha256()
for chunk in iter(lambda: file.read(8192), b""):
    h.update(chunk)
file_hash = h.hexdigest()
file.seek(0)  # 复位供后续 R2 上传
```

### 7.4 CSV injection 防护（失败明细下载）
```python
def _csv_safe(value: str) -> str:
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value   # 前缀单引号，防 Excel 公式执行
    return value
```

---

## 8. 测试栈

| 工具 | 用途 |
|---|---|
| pytest + pytest-asyncio | 复用 U01 套件 |
| FakeImportAdapter | 内存 upsert + 可配置第 N 行失败 + 幂等，验证框架编排 |
| 同步调用 run_import_batch | 不经 Celery broker，直接 await 任务内部 async 函数 |
| freezegun | 退避时间 / 时间戳断言 |
| 跨租户 fixture | 验证 worker SET app.tenant_id + RLS 拦截跨租户 upsert |
| 临时 CSV/XLSX 构造 | io.StringIO / openpyxl 写测试文件 |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 唯一新增依赖 openpyxl | ✅ §2 |
| 复用 U01 R2 helper（不依赖 U05 ORM，FB-A） | ✅ §5 |
| 复用 default Celery 队列（FB Q4） | ✅ §4 |
| 任务级不 autoretry 业务失败（FB-E） | ✅ §4 |
| 5 个 Prometheus 指标 | ✅ §6 |
| openpyxl read_only + data_only 防宏/注入（FB Q11） | ✅ §7.2 |
| CSV injection 转义（FB Q11） | ✅ §7.4 |
| 新增 4 个配置项 | ✅ §3 |
