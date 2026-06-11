# U06d PromotionImportAdapter 规格（Adapter Spec）

> source: `manual_promotion` / target_table: `promotion`
> 文件: `backend/app/modules/importer/adapters/promotion.py`

---

## 1. 三方法签名

```python
class PromotionImportAdapter:
    source = "manual_promotion"
    target_table = "promotion"
    def __init__(self): self._tenant_code_cache = {}   # tenant_id → code

    def parse_row(self, row, mapping) -> dict          # _to_date / _to_decimal / str
    def validate(self, parsed) -> list[str]            # 必填/数值/date（不查 FK）
    async def upsert(self, parsed, *, session, tenant_id, actor_id) -> tuple[UUID, bool]
                                                       # FK 解析 → internal_code → INSERT
```

## 2. 默认字段映射（_DEFAULT_COLUMNS，mapping=None 回退）

| source_col | target_field | type | required |
|---|---|---|---|
| 款式编码 | style_code | str | ✅（FK） |
| SKU编码 | sku_code | str | —（可选 FK） |
| 小红书ID | xiaohongshu_id | str | ✅（FK） |
| 报价金额 | quote_amount | decimal | ✅ |
| 成本快照 | cost_snapshot | decimal | — |
| 平台 | platform | str | —（默认"小红书"） |
| 合作日期 | cooperation_date | date | ✅（internal_code 前缀） |
| 计划发布日期 | scheduled_publish_date | date | — |
| 笔记标题 | note_title | str | — |
| 备注 | remark | str | — |

## 3. parse_row 类型转换

| type | 转换 | 空 |
|---|---|---|
| str | strip | "" → None |
| decimal | `_to_decimal`：去千分位 + Decimal（禁 float）；非法 → 原串 | "" → None |
| date | `_to_date`：date.fromisoformat（YYYY-MM-DD）；非法 → 原串 | "" → None |

## 4. validate 规则（纯函数，不查 FK）

| 校验 | 文案 |
|---|---|
| style_code / xiaohongshu_id 必填 | `款式编码不能为空` / `小红书ID不能为空` |
| quote_amount 必填且 Decimal ≥0 | `报价金额不能为空` / `报价金额必须为非负数字` |
| cost_snapshot 非空时 Decimal ≥0 | `成本快照必须为非负数字` |
| cooperation_date 必填且合法 date | `合作日期不能为空` / `合作日期格式错误（应为 YYYY-MM-DD）` |
| scheduled_publish_date 非空时合法 date | `计划发布日期格式错误（应为 YYYY-MM-DD）` |
| 长度上限 | `<字段> 超过长度上限 N` |

## 5. upsert 流程（INSERT-only + FK 解析）

```
1. StyleRepository.get_by_code(style_code) → 无 → RowValidationError(款式编码 X 不存在)
2. BloggerRepository.get_by_xiaohongshu_id(xhs) → 无 → RowValidationError(博主 X 不存在)
3. sku_code 非空 → SkuRepository.get_by_code → 无 → RowValidationError(SKU编码 X 不存在)
4. tenant_code = _get_tenant_code(缓存)
5. seq = next_internal_sequence(tenant_id, cooperation_date)  # U04 FB2 原子（溢出 raise）
6. internal_code = format_internal_code(tenant_code, cooperation_date, seq)
7. Promotion(... 快照 + pr_id=actor + 3 状态 server_default) add + flush
8. return (promotion.id, True)
```

- 不 commit（runner 控制）；FK+sequence+INSERT 同 per-row 事务原子
- FK 缺失 / 序列溢出 → RowValidationError（runner 捕获 → import_job.failed）
- tenant_id：runner SET LOCAL（NF-1）+ ORM 钩子注入
- _get_tenant_code 实例级缓存（tenant.code 不可变）

## 6. 注册
```python
def register() -> None:
    ImportAdapterRegistry.register(PromotionImportAdapter())
```
由 U06a register_import_adapters 双进程调用（main.py 已预置 adapters.promotion 路径）。

## 7. 使用（PR 视角）
```
POST /api/imports/upload (multipart) source=manual_promotion, file=推广清单.csv（默认表头见 §2）
前置：导入的 style_code / xiaohongshu_id 须已存在于系统（先导商品/博主）
→ 202 {batch_id, status: processing} → 异步 run_import_batch → 逐行 FK 解析 + 建 promotion
→ GET /api/imports/batches/{id} 查看 / errors/download 下载 / retry 重试
```
