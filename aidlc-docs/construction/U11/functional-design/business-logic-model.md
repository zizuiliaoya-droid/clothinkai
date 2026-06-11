# U11 业务逻辑模型（博主智能标签 + 灰豚展示）

> 单元：U11 — EP04-S04~S08；6 个 Use Case + 跨单元契约

---

## 1. 用例总览

| UC | 动作 | 触发 | 故事 |
|---|---|---|---|
| UC-1 | compute_blogger_type | 同步（blogger 创建/更新 follower） | S04 |
| UC-2 | compute_read_like_ratio | 读时计算（get_detail） | S05 |
| UC-3 | is_fake_account | recompute 任务 | S06 |
| UC-4 | compute_quality_tags | recompute 任务 | S07 |
| UC-5 | recompute_all_blogger_tags | Celery 任务 / admin 端点 | S04~S07 |
| UC-6 | audience_profile 展示 | get_detail 读 | S08 |

---

## 2. 核心用例流程

### UC-1 compute_blogger_type
```
1. 输入：blogger.follower_count
2. if null → type = null
   elif < KOC_MIN → "素人"
   elif < KOL_MIN → "KOC"
   else → "KOL"
3. UPDATE blogger SET blogger_type = type
```
触发点：BloggerService.create_blogger / update_blogger 修改 follower_count 后调用。

### UC-2 compute_read_like_ratio（读时衍生）
```
1. ap = blogger.audience_profile
2. if ap is None → null
3. avg_reads = ap["note_stats"]["avg_reads"]
4. if avg_reads == 0 → null
5. return ap["note_stats"]["avg_likes"] / avg_reads
```
不存 DB；BloggerResponse 新增 read_like_ratio 字段。

### UC-5 recompute_all_blogger_tags（Celery）
```
1. 逐 tenant（system_context）
2. 逐 active blogger
3. compute_blogger_type → save
4. compute_read_like_ratio → 若非 null 判 is_fake → save is_suspected_fake
5. 查 promotion 历史 → compute_quality_tags → save
6. return 更新总数
```
触发：admin POST /api/bloggers/recompute-tags（入队 Celery）或 Beat 每日 02:00。

### UC-6 audience_profile 展示
```
GET /api/bloggers/{id} → BloggerResponse.audience_profile
  = blogger.audience_profile（JSONB 原样返回，null = 未同步）
```

---

## 3. 跨单元契约

| 依赖 | 复用 | 用途 |
|---|---|---|
| U03 Blogger model + BloggerService | 已有字段 + 创建/更新触发点 | UC-1 |
| U04 PromotionRepository | 查询历史推广（avg CPL / hit rate 按 blogger） | UC-4 |
| U13 灰豚 adapter（未来） | 写 blogger.audience_profile JSONB | UC-6 数据源 |
| U01 celery_app + system_context | Celery 任务 + 逐租户 | UC-5 |
| U01 audit | recompute 写 audit | UC-5 |

---

## 4. BloggerResponse 追加字段

```python
# BloggerResponse（U03 扩展）
audience_profile: dict | None = None      # U11 灰豚画像 JSONB
read_like_ratio: Decimal | None = None    # U11 衍生（读时计算）
```

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 覆盖 EP04-S04~S08 | ✅ |
| 与 application-design BloggerTagService 方法签名一致 | ✅ |
| 分母 0 → null（BR-U11-11/21） | ✅ |
| 批量 Celery + admin 触发 | ✅ |
| audience_profile 由 U13 写入，U11 仅读 | ✅ |
| 复用 U03 已有字段，唯一 DDL = ALTER ADD audience_profile | ✅ |
