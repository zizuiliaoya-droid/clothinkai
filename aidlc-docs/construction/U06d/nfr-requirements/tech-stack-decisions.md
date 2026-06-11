# U06d 技术栈决策（Tech Stack Decisions）

> 单元：U06d — 推广导入适配器
> 定位：纯适配器层 — **无新依赖 / 无新服务 / 无新配置 / 无新指标 / 无新表 / 无新端点**

---

## 1. 决策总览

| 维度 | 决策 | 来源 |
|---|---|---|
| 文件解析 | 复用 U06a openpyxl + csv | U06a runner |
| 数值类型 | Decimal（禁 float，去千分位） | 标准库 |
| 日期类型 | `date.fromisoformat`（YYYY-MM-DD） | 标准库（_to_date 新增） |
| FK 解析 | StyleRepository.get_by_code + BloggerRepository.get_by_xiaohongshu_id + SkuRepository.get_by_code | U02 / U03 |
| internal_code | next_internal_sequence（FB2）+ format_internal_code | U04 |
| promotion 写入 | PromotionRepository.add + flush（INSERT-only） | U04 |
| 注册 | 复用 register_import_adapters（main.py 已含 adapters.promotion） | U06a NF-4 |
| 端点/重试/下载/映射/权限/配置/指标/测试 | 复用 U06a | U06a |

---

## 2. 唯一技术增量

### 2.1 _to_date（标准库）
```python
from datetime import date

def _to_date(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return date.fromisoformat(str(raw).strip())  # YYYY-MM-DD
    except ValueError:
        return str(raw)  # 非法 → validate 检出
```

### 2.2 tenant_code 实例级缓存
```python
# adapter 实例持有 _tenant_code_cache: dict[UUID, str]
# tenant.code 不可变 → 缓存安全；避免每行查 tenant 表
```

### 2.3 唯一新文件
- `backend/app/modules/importer/adapters/promotion.py`（PromotionImportAdapter + _DEFAULT_COLUMNS + _to_date + _to_decimal + tenant_code 缓存 + register()）

> main.py 已含 `app.modules.importer.adapters.promotion`（U06a 预置）→ 落地后自动注册，**main.py / celery_app.py 不改**。

---

## 3. 明确不引入

| 项 | 理由 |
|---|---|
| 新依赖 | Decimal/date 标准库；openpyxl/csv 复用 U06a |
| 新 Celery / API / 表 / migration | 复用 U04 promotion + U06a 框架/端点/runner |
| 新权限/配置/指标 | 复用 U06a |
| 新 UNIQUE 约束（dedup 键） | INSERT-only 跨文件不去重（已知限制，V1 评估） |
| style/blogger 批量预解析缓存 | MVP 行级查询；批量优化留 V1 |

---

## 4. 测试技术栈
- 复用 U06a/b/c pytest 基建（真实 PromotionImportAdapter；样本 CSV fixture；直接 await _run_import_batch + monkeypatch session + mock get_object_bytes + committed 清理含 promotion/promotion_sequence/seed style+blogger）

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新依赖（Decimal/date 标准库） | ✅ §2 |
| 复用 U04 sequence/format_internal_code + U02/U03 FK | ✅ §1 |
| 复用 U06a 框架 | ✅ §1 + §3 |
| 唯一增量 = adapters/promotion.py | ✅ §2.3 |
| main.py/celery_app.py 不改 | ✅ §2.3 |
