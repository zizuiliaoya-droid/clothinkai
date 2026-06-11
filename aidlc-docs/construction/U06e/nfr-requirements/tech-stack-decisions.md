# U06e 技术栈决策（Tech Stack Decisions）

> 单元：U06e — 结算导入适配器
> 定位：纯适配器层 — **无新依赖 / 无新服务 / 无新配置 / 无新指标 / 无新表 / 无新端点**

---

## 1. 决策总览

| 维度 | 决策 | 来源 |
|---|---|---|
| 文件解析 | 复用 U06a openpyxl + csv | U06a runner |
| 数值类型 | Decimal（禁 float，去千分位） | 标准库 |
| 日期类型 | `date.fromisoformat`（_to_date，同 U06d） | 标准库 |
| 状态枚举校验 | SettlementStatus（U05 enums） | U05 |
| promotion 查询 | PromotionRepository.get_by_internal_code | U04 |
| settlement 写入 | SettlementRepository.add + flush（INSERT-only）+ next_settlement_sequence | U05 |
| settlement_no | format_settlement_no | U05 domain |
| 合成 event_id | `uuid4()` | 标准库 |
| 注册 | 复用 register_import_adapters（main.py 已含 adapters.settlement） | U06a NF-4 |
| 端点/重试/下载/映射/权限/配置/指标/测试 | 复用 U06a | U06a |

---

## 2. 唯一技术增量

### 2.1 标准库工具（无新依赖）
- `_to_date`（date.fromisoformat，同 U06d）
- `_to_decimal`（去千分位 + Decimal，禁 float）
- `request_event_id = uuid4()`（合成）
- tenant_code 实例级缓存（dict[UUID,str]）

### 2.2 状态枚举校验
```python
from app.modules.finance.enums import SettlementStatus
_VALID_STATUS = {s.value for s in SettlementStatus}  # 5 枚举
```

### 2.3 UNIQUE(promotion_id) 冲突处理
```python
from sqlalchemy.exc import IntegrityError
from app.modules.importer.exceptions import RowValidationError
try:
    repo.add(settlement); await session.flush()
except IntegrityError as exc:
    raise RowValidationError("该推广已有结算单（不可重复，FB3）") from exc
```
> per-row 事务隔离：IntegrityError 后该行 savepoint 回滚（runner 控制），不影响其他行。

### 2.4 唯一新文件
- `backend/app/modules/importer/adapters/settlement.py`（SettlementImportAdapter + _DEFAULT_COLUMNS + _to_date + _to_decimal + _get_tenant_code + register()）

> main.py 已含 `app.modules.importer.adapters.settlement`（U06a 预置）→ 落地后自动注册，**main.py / celery_app.py 不改**。

---

## 3. 明确不引入

| 项 | 理由 |
|---|---|
| 新依赖 | Decimal/date/uuid4 标准库；openpyxl/csv 复用 U06a |
| 新 Celery / API / 表 / migration | 复用 U05 settlement + U06a 框架 |
| 新权限/配置/指标 | 复用 U06a |
| 事件触发 | 导入是数据迁移，不调 event_bus（不经 U05 Service） |
| extra_item 创建 | 历史迁移仅 settlement 主记录；extra_item 迁移留 V1 |
| payment_proof 附件迁移 | 留 None；附件迁移 V1 |

---

## 4. 测试技术栈
- 复用 U06a/b/c/d pytest 基建（真实 SettlementImportAdapter；样本 CSV fixture；直接 await _run_import_batch + monkeypatch session + mock get_object_bytes + committed 清理含 settlement/settlement_sequence/seed promotion+blogger+style；event_capture 断言不触发事件）

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新依赖（Decimal/date/uuid4 标准库） | ✅ §2 |
| 复用 U05 sequence/format_settlement_no + U04 promotion 查询 | ✅ §1 |
| 复用 U06a 框架 | ✅ §1 + §3 |
| 唯一增量 = adapters/settlement.py | ✅ §2.4 |
| 不触发事件 / 不经 Service | ✅ §3 |
| main.py/celery_app.py 不改 | ✅ §2.4 |
