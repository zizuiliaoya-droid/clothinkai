# U11 NFR 设计模式（NFR Design Patterns）

> 单元：U11 — 博主智能标签 + 灰豚展示
> 模式：P-U11-01（标签计算+Celery 容错）、P-U11-02（audience 读展示+聚合）

---

## P-U11-01 — BloggerTagService 实时+批量计算

### 阈值常量（tag_config.py）

```python
from decimal import Decimal

FOLLOWER_KOC_MIN = 10_000
FOLLOWER_KOL_MIN = 100_000
FAKE_RATIO_THRESHOLD = Decimal("0.01")
HIGH_CPL_THRESHOLD = Decimal("5.00")
HIT_RATE_THRESHOLD = Decimal("0.20")
```

### compute_blogger_type（实时，O(1)）

```python
def compute_blogger_type(follower_count: int | None) -> str | None:
    if follower_count is None:
        return None
    if follower_count >= FOLLOWER_KOL_MIN:
        return "KOL"
    if follower_count >= FOLLOWER_KOC_MIN:
        return "KOC"
    return "素人"
```

触发：BloggerService 创建/更新 follower → `blogger.blogger_type = compute_blogger_type(blogger.follower_count)`。

### recompute_all_blogger_tags（Celery，逐 tenant 容错）

```python
@celery_app.task(bind=True, autoretry_for=(OperationalError,), max_retries=2, default_retry_delay=10)
def recompute_all_blogger_tags(self):
    asyncio.run(_recompute_impl())

async def _recompute_impl():
    tenants = await get_all_active_tenants()
    for t in tenants:
        async with system_context(t.id):
            bloggers = await repo.list_active_bloggers(t.id)
            updated = 0; failed = 0
            for b in bloggers:
                try:
                    b.blogger_type = compute_blogger_type(b.follower_count)
                    ratio = compute_read_like_ratio(b.audience_profile)
                    b.is_suspected_fake = is_fake_account(ratio)
                    b.quality_tags = await compute_quality_tags(b.id, session, t.id)
                    updated += 1
                except Exception:
                    log.warning("recompute_blogger_failed", blogger_id=str(b.id))
                    failed += 1
            await session.commit()
            log.info("recompute_done", tenant=str(t.id), updated=updated, failed=failed)
```

- 单 blogger 失败不中止（catch+log+继续）。
- 返回 updated/failed 计数（admin 端点可查）。

---

## P-U11-02 — audience_profile 读展示 + 聚合

### read_like_ratio（读时衍生，不存 DB）

```python
def compute_read_like_ratio(audience_profile: dict | None) -> Decimal | None:
    if audience_profile is None:
        return None
    stats = audience_profile.get("note_stats")
    if not stats:
        return None
    avg_reads = stats.get("avg_reads", 0)
    if avg_reads == 0:
        return None
    return Decimal(str(stats.get("avg_likes", 0))) / Decimal(str(avg_reads))
```

### is_fake_account

```python
def is_fake_account(ratio: Decimal | None) -> bool:
    if ratio is None:
        return False  # 无数据 → 不标记（保守）
    return ratio <= FAKE_RATIO_THRESHOLD
```

### quality_tags 聚合（services/metric/blogger_quality.py）

```python
async def compute_quality_tags(blogger_id, session, tenant_id) -> list[str]:
    tags = []
    cpl = await avg_cpl_for_blogger(blogger_id, session, tenant_id)
    if cpl is not None and cpl <= HIGH_CPL_THRESHOLD:
        tags.append("高性价比")
    hit = await hit_rate_for_blogger(blogger_id, session, tenant_id)
    if hit is not None and hit >= HIT_RATE_THRESHOLD:
        tags.append("带货型")
    return tags

async def avg_cpl_for_blogger(blogger_id, session, tenant_id) -> Decimal | None:
    # SELECT AVG(quote_amount / NULLIF(effective_like_count,0))
    # FROM promotion WHERE tenant_id=:tid AND blogger_id=:bid AND is_active LIMIT 1000
    ...

async def hit_rate_for_blogger(blogger_id, session, tenant_id) -> Decimal | None:
    # COUNT(like_count >= HIT_THRESHOLD) / NULLIF(COUNT(*),0)
    ...
```

- 显式 WHERE tenant_id 防御 + 测试确定性。
- LIMIT 1000 截断超大历史。
- safe_div（复用 services/metric/common.py）。

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| type 实时 O(1) + 触发集成 | ✅ P-U11-01 |
| recompute 逐 tenant 容错 + autoretry | ✅ P-U11-01 |
| ratio 分母 0 → null | ✅ P-U11-02 |
| fake 无数据 → false（保守） | ✅ P-U11-02 |
| quality 聚合显式 tenant + LIMIT | ✅ P-U11-02 |
| audience_profile null → null 安全 | ✅ P-U11-02 |
