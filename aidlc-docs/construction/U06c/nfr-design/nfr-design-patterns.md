# U06c NFR 设计模式（NFR Design Patterns）

> 单元：U06c — 博主导入适配器
> 范围：1 个增量模式 P-U06c-01（单实体 Blogger upsert + 多类型解析）；其余继承 U06a P-U06a-01~05 + U06b P-U06b-01 + U03 upsert_atomic
> 关键：单实体（无 U06b 的 style get-or-create / brand 关联）；adapter 不自 commit（FB-C）

---

## 0. 继承声明

| 模式 | 来源 | 依赖点 |
|---|---|---|
| P-U06a-01 Runner 事务 + 租户上下文（per-row SET LOCAL，NF-1） | U06a | adapter 在该事务内执行 |
| P-U06a-02 Adapter 协议 + Registry（NF-4 双进程注册） | U06a | 实现 + register() |
| P-U06a-03/04/05 上传/重试/安全 | U06a | 框架处理 |
| P-U06b-01 单行 upsert 编排 | U06b | 简化为单实体（无 style/brand 步骤） |
| U03 upsert_atomic（ON CONFLICT xiaohongshu_id RETURNING） | U03 | blogger 单次 upsert |

---

## P-U06c-01：单实体 Blogger upsert + 多类型解析

### 问题
博主导入比商品/SKU 简单（单实体），但引入了 U06b 没有的**多种类型转换**：
- list（标签分隔串 → JSONB 数组）
- int（follower_count）
- Decimal（quote，禁 float）

需保证：adapter 不自 commit（FB-C）；单次 upsert 幂等（U03 ON CONFLICT）；类型解析正确；platform 默认值不被 UPDATE 路径覆盖为空。

### 方案

```python
# modules/importer/adapters/blogger.py
import re
from decimal import Decimal, InvalidOperation

_TAG_SEP = re.compile(r"[;；,，]")
_DEFAULT_COLUMNS = [  # 13 列（domain-entities §4）
    {"source_col": "小红书ID", "target_field": "xiaohongshu_id", "type": "str"},
    {"source_col": "昵称", "target_field": "nickname", "type": "str"},
    {"source_col": "平台", "target_field": "platform", "type": "str"},
    {"source_col": "微信", "target_field": "wechat", "type": "str"},
    {"source_col": "手机号", "target_field": "phone", "type": "str"},
    {"source_col": "粉丝数", "target_field": "follower_count", "type": "int"},
    {"source_col": "博主类型", "target_field": "blogger_type", "type": "str"},
    {"source_col": "性别投放", "target_field": "gender_target", "type": "str"},
    {"source_col": "类目标签", "target_field": "category_tags", "type": "list"},
    {"source_col": "质量标签", "target_field": "quality_tags", "type": "list"},
    {"source_col": "报价", "target_field": "quote", "type": "decimal"},
    {"source_col": "合作历史", "target_field": "cooperation_history", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
]
_REQUIRED = (("xiaohongshu_id", "小红书ID"), ("nickname", "昵称"))
_MAX_LEN = (("xiaohongshu_id", 64), ("nickname", 128), ("wechat", 64),
            ("phone", 32), ("platform", 16), ("blogger_type", 16), ("gender_target", 16))


def _split_tags(raw):
    if raw is None or str(raw).strip() == "":
        return []
    return [t.strip() for t in _TAG_SEP.split(str(raw)) if t.strip()]


def _to_int(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return str(raw)  # 非法 → validate 检出


def _to_decimal(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())  # 禁 float
    except InvalidOperation:
        return str(raw)


class BloggerImportAdapter:
    source = "manual_blogger"
    target_table = "blogger"

    def parse_row(self, row, mapping):
        columns = (mapping.mapping_config["columns"] if mapping else _DEFAULT_COLUMNS)
        parsed = {}
        for col in columns:
            raw = row.get(col["source_col"])
            t = col.get("type", "str")
            tgt = col["target_field"]
            if t == "list":
                parsed[tgt] = _split_tags(raw)
            elif t == "int":
                parsed[tgt] = _to_int(raw)
            elif t == "decimal":
                parsed[tgt] = _to_decimal(raw)
            else:
                parsed[tgt] = (str(raw).strip() if raw not in (None, "") else None)
        return parsed

    def validate(self, parsed):
        errs = []
        for f, label in _REQUIRED:
            if not parsed.get(f):
                errs.append(f"{label}不能为空")
        fc = parsed.get("follower_count")
        if fc is not None and (not isinstance(fc, int) or fc < 0):
            errs.append("粉丝数必须为非负整数")
        q = parsed.get("quote")
        if q is not None and (not isinstance(q, Decimal) or q < 0):
            errs.append("报价必须为非负数字")
        for f, n in _MAX_LEN:
            v = parsed.get(f)
            if v and isinstance(v, str) and len(v) > n:
                errs.append(f"{f} 超过长度上限 {n}")
        return errs

    async def upsert(self, parsed, *, session, tenant_id, actor_id):
        repo = BloggerRepository(session)
        values = {
            "xiaohongshu_id": parsed["xiaohongshu_id"],
            "nickname": parsed["nickname"],
            "platform": parsed.get("platform") or "小红书",  # 默认，防 UPDATE 覆盖空
            "wechat": parsed.get("wechat"),
            "phone": parsed.get("phone"),
            "follower_count": parsed.get("follower_count"),
            "blogger_type": parsed.get("blogger_type"),
            "gender_target": parsed.get("gender_target"),
            "category_tags": parsed.get("category_tags") or [],
            "quality_tags": parsed.get("quality_tags") or [],
            "quote": parsed.get("quote"),
            "cooperation_history": parsed.get("cooperation_history"),
            "remark": parsed.get("remark"),
        }
        blogger, is_inserted = await repo.upsert_atomic(
            tenant_id=tenant_id, values=values
        )
        return blogger.id, is_inserted


def register() -> None:
    ImportAdapterRegistry.register(BloggerImportAdapter())
```

### 关键点
- **单实体**：upsert 仅一次 upsert_atomic（无 U06b style get-or-create / brand 关联）
- **不自 commit**：复用 runner 传入 session（P-U06a-01）
- **list → JSONB**：_split_tags 多分隔符；空 → []（SQLAlchemy JSONB 列接受 Python list）
- **int/Decimal 禁 float**：非法值保留原串 → validate 检出
- **platform 默认显式传 "小红书"**：避免 ON CONFLICT UPDATE 路径用空覆盖已有 platform
- **actor_id 不写业务表**：U03 blogger 无 created_by（仅审计上下文）

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| adapter 不自 commit（FB-C） | ✅ upsert 仅用传入 session |
| 单实体单次 upsert | ✅ 1 次 upsert_atomic |
| upsert 幂等（U03 ON CONFLICT） | ✅ |
| list → JSONB 数组 | ✅ _split_tags |
| int/Decimal 禁 float | ✅ _to_int/_to_decimal |
| platform 默认不被覆盖 | ✅ 显式传 "小红书" |
| 跨租户 tenant_id（NF-1） | ✅ runner SET LOCAL + upsert_atomic 显式 tenant_id |
| 注册（NF-4 双进程） | ✅ register() + U06a register_import_adapters |
