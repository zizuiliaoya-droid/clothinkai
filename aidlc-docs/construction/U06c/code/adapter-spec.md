# U06c BloggerImportAdapter 规格（Adapter Spec）

> source: `manual_blogger` / target_table: `blogger`
> 文件: `backend/app/modules/importer/adapters/blogger.py`

---

## 1. 三方法签名（实现 U06a ImportAdapter 协议）

```python
class BloggerImportAdapter:
    source = "manual_blogger"
    target_table = "blogger"

    def parse_row(self, row, mapping) -> dict          # _split_tags / _to_int / _to_decimal
    def validate(self, parsed) -> list[str]
    async def upsert(self, parsed, *, session, tenant_id, actor_id) -> tuple[UUID, bool]
                                                       # 单次 BloggerRepository.upsert_atomic
```

## 2. 默认字段映射（_DEFAULT_COLUMNS，mapping=None 回退）

| source_col | target_field | type | required |
|---|---|---|---|
| 小红书ID | xiaohongshu_id | str | ✅ |
| 昵称 | nickname | str | ✅ |
| 平台 | platform | str | — |
| 微信 | wechat | str | — |
| 手机号 | phone | str | — |
| 粉丝数 | follower_count | int | — |
| 博主类型 | blogger_type | str | — |
| 性别投放 | gender_target | str | — |
| 类目标签 | category_tags | list | — |
| 质量标签 | quality_tags | list | — |
| 报价 | quote | decimal | — |
| 合作历史 | cooperation_history | str | — |
| 备注 | remark | str | — |

## 3. parse_row 类型转换

| type | 转换 | 空 |
|---|---|---|
| str | strip | "" → None |
| int | `_to_int`：去千分位 + int；非法 → 原串 | "" → None |
| decimal | `_to_decimal`：去千分位 + Decimal（禁 float）；非法 → 原串 | "" → None |
| list | `_split_tags`：按 `;；,，` 拆分 + strip + 去空 | 空 → [] |

## 4. validate 规则

| 校验 | 文案 |
|---|---|
| xiaohongshu_id / nickname 必填 | `小红书ID不能为空` / `昵称不能为空` |
| follower_count 是 int 且 ≥0 | `粉丝数必须为非负整数` |
| quote 是 Decimal 且 ≥0 | `报价必须为非负数字` |
| 长度上限（xiaohongshu_id≤64/nickname≤128/wechat≤64/phone≤32/platform≤16/blogger_type≤16/gender_target≤16） | `<字段> 超过长度上限 N` |

## 5. upsert 流程（单实体）

```
BloggerRepository(session).upsert_atomic(tenant_id, values{
  xiaohongshu_id, nickname, platform(空→"小红书"), wechat, phone,
  follower_count, blogger_type, gender_target,
  category_tags(空→[]), quality_tags(空→[]), quote, cooperation_history, remark
}) → (blogger, is_inserted)
return (blogger.id, is_inserted)
```

- 不 commit（runner 控制）；单次 upsert（无关联实体）
- ON CONFLICT(tenant_id, xiaohongshu_id) DO UPDATE（复用 U03）
- platform 显式传 "小红书"（防 UPDATE 覆盖空）
- tenant_id：runner SET LOCAL（NF-1）+ ORM 钩子 + upsert_atomic 显式传

## 6. 注册

```python
def register() -> None:
    ImportAdapterRegistry.register(BloggerImportAdapter())
```
由 U06a register_import_adapters 双进程调用（main.py 已预置 adapters.blogger 路径）。

## 7. 使用（运营视角）
```
POST /api/imports/upload (multipart) source=manual_blogger, file=博主清单.csv（默认表头见 §2）
→ 202 {batch_id, status: processing} → 异步 run_import_batch → 逐行 blogger upsert
→ GET /api/imports/batches/{id} 查看 / errors/download 下载失败 / retry 重试
```
