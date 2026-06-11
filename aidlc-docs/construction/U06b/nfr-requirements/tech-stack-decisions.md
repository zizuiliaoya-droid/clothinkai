# U06b 技术栈决策（Tech Stack Decisions）

> 单元：U06b — 商品/SKU 导入适配器
> 定位：纯适配器层 — **无新依赖 / 无新服务 / 无新配置 / 无新指标 / 无新表 / 无新端点**

---

## 1. 决策总览

| 维度 | 决策 | 来源 |
|---|---|---|
| 文件解析 | 复用 U06a `openpyxl`（XLSX read_only）+ 标准库 `csv` | U06a runner `_parse_rows` |
| 数值类型 | `decimal.Decimal`（**禁用 float**）；解析前去千分位 `,` | U06b 新增（标准库，无依赖） |
| style 写入 | 复用 U02 `StyleRepository.get_by_code` + `session.add(Style(...))` | U02 |
| sku 写入 | 复用 U02 `SkuRepository.upsert_atomic`（ON CONFLICT RETURNING is_inserted） | U02 P-U02-03 |
| brand 关联 | 按 `(tenant, brand_code)` 查 brand_id（软关联，查不到留空） | U02 Brand |
| 注册机制 | 复用 U06a `register_import_adapters`（main.py lifespan + worker_process_init） | U06a NF-4 |
| 上传/重试/下载/映射 | 复用 U06a 8 端点（source=manual_style_sku） | U06a api.py |
| 权限 | 复用 U06a `importer.batch:read/write` + `importer.mapping:write` | U06a NF-5 |
| 配置 | 复用 U06a `IMPORT_MAX_FILE_MB` / `IMPORT_MAX_ROWS` / `IMPORT_BUCKET` | U06a config |
| 指标 | 复用 U06a 5 指标（label source=manual_style_sku） | U06a metrics |
| 测试 | 复用 U06a 测试基建（真实 adapter + 样本 CSV fixture + 同步任务调用 + monkeypatch session） | U06a conftest |

---

## 2. 唯一技术增量

### 2.1 Decimal 解析（标准库，无新依赖）
```python
from decimal import Decimal, InvalidOperation

def _parse_decimal(raw: str | None) -> Decimal | None:
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(",", "").strip()  # 去千分位
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"无法解析为数字: {raw}") from exc
    return value  # 负数校验在 validate 层（统一错误文案）
```
- **禁用 float**：避免 `float("0.1")+float("0.2")` 类精度丢失；价格直接 Decimal
- 非法值抛 ValueError → parse_row 捕获或 validate 拦截 → import_job.failed

### 2.2 唯一新文件
- `backend/app/modules/importer/adapters/__init__.py`（包初始化，可能 U06a 已建）
- `backend/app/modules/importer/adapters/style_sku.py`（StyleSkuImportAdapter + 内置默认映射 + `register()`）

> main.py 的 `register_import_adapters` 已含 `app.modules.importer.adapters.style_sku`（U06a 预置，ModuleNotFoundError 仅 warning）→ 本文件落地后自动注册，**main.py / celery_app.py 不改**。

---

## 3. 明确不引入

| 项 | 理由 |
|---|---|
| 新 Python 依赖 | openpyxl/csv 复用 U06a；Decimal 是标准库 |
| 新 Celery 队列/任务 | 复用 U06a run_import_batch（runner 不改） |
| 新 API 端点 | 复用 U06a 8 端点（upload 传 source=manual_style_sku） |
| 新数据库表/migration | 复用 U02 style/sku/brand + U06a import_* 表 |
| 新权限 scope | 复用 importer.batch/mapping |
| 新配置项 | 复用 IMPORT_MAX_* |
| 新 Prometheus 指标 | 复用 U06a 5 指标（source label 区分） |
| 批量 upsert | MVP 行级独立事务优先（FB-C）；批量优化留 V1 |
| advisory lock（style 并发建） | MVP 接受罕见并发 failed（retry 复用）；不引入锁 |

---

## 4. 测试技术栈
- 复用 U01/U06a pytest + pytest-asyncio + 共享测试 DB
- 真实 `StyleSkuImportAdapter`（不用 FakeImportAdapter）
- 样本 CSV fixture（domain-entities / business-logic-model 的 3 行验收样本）
- 集成测试：直接 `await _run_import_batch`（monkeypatch AsyncSessionApp/Bypass → 测试 engine + mock `attachment_service.get_object_bytes` 注入 CSV bytes）+ committed 数据 + finally 清理（仿 U06a test_import_runner）
- unit：parse_row / validate 纯函数（无 DB）

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新依赖（Decimal 标准库） | ✅ §2.1 |
| 复用 U02 upsert_atomic + StyleRepository | ✅ §1 |
| 复用 U06a 框架（端点/runner/指标/权限/配置/注册） | ✅ §1 + §3 |
| 唯一增量 = adapters/style_sku.py | ✅ §2.2 |
| main.py/celery_app.py 不改 | ✅ §2.2（U06a 预置模块路径） |
