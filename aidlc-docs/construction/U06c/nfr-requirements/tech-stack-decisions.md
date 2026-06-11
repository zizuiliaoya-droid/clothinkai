# U06c 技术栈决策（Tech Stack Decisions）

> 单元：U06c — 博主导入适配器
> 定位：纯适配器层 — **无新依赖 / 无新服务 / 无新配置 / 无新指标 / 无新表 / 无新端点**

---

## 1. 决策总览

| 维度 | 决策 | 来源 |
|---|---|---|
| 文件解析 | 复用 U06a openpyxl + csv | U06a runner |
| 数值类型 | int（标准库）+ Decimal（禁 float，复用 U06b _to_decimal） | U06b/标准库 |
| 标签解析 | `_split_tags`（标准库 str.split，多分隔符 `;；,，`） | U06c 新增（无依赖） |
| blogger 写入 | 复用 U03 `BloggerRepository.upsert_atomic`（ON CONFLICT xiaohongshu_id） | U03 |
| 注册 | 复用 U06a register_import_adapters（main.py 已含 adapters.blogger） | U06a NF-4 |
| 端点/重试/下载/映射 | 复用 U06a 8 端点（source=manual_blogger） | U06a |
| 权限 | 复用 importer.batch:read/write + importer.mapping:write | U06a NF-5 |
| 配置/指标/测试 | 复用 U06a（IMPORT_MAX_* / 5 指标 / 测试基建） | U06a |

---

## 2. 唯一技术增量

### 2.1 _split_tags（标准库，无依赖）
```python
import re

_TAG_SEP = re.compile(r"[;；,，]")

def _split_tags(raw):
    if raw is None or str(raw).strip() == "":
        return []
    return [t.strip() for t in _TAG_SEP.split(str(raw)) if t.strip()]
```

### 2.2 _to_int（标准库）
```python
def _to_int(raw):
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return str(raw)  # 非法 → validate 检出
```

### 2.3 唯一新文件
- `backend/app/modules/importer/adapters/blogger.py`（BloggerImportAdapter + 内置默认映射 + _split_tags + _to_int + _to_decimal + register()）

> main.py 已含 `app.modules.importer.adapters.blogger` 路径（U06a 预置）→ 落地后自动注册，**main.py / celery_app.py 不改**。

---

## 3. 明确不引入

| 项 | 理由 |
|---|---|
| 新依赖 | re/int/Decimal 标准库；openpyxl/csv 复用 U06a |
| 新 Celery 队列/任务 | 复用 run_import_batch |
| 新 API 端点 | 复用 U06a 8 端点 |
| 新表/migration | 复用 U03 blogger + U06a import_* |
| 新权限/配置/指标 | 复用 U06a |

---

## 4. 测试技术栈
- 复用 U06a/U06b pytest 基建（真实 BloggerImportAdapter；样本 CSV fixture；直接 await _run_import_batch + monkeypatch session + mock get_object_bytes + committed 清理）

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新依赖（_split_tags/int/Decimal 标准库） | ✅ §2 |
| 复用 U03 upsert_atomic | ✅ §1 |
| 复用 U06a 框架 | ✅ §1 + §3 |
| 唯一增量 = adapters/blogger.py | ✅ §2.3 |
| main.py/celery_app.py 不改 | ✅ §2.3 |
