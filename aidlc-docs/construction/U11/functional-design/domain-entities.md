# U11 领域实体（博主智能标签 + 灰豚展示）

> 单元：U11 — EP04-S04~S08
> 依赖：U03（Blogger 已有 blogger_type/quality_tags/is_suspected_fake）、U13（灰豚写入 audience_profile）

---

## 1. 实体变更

| 对象 | 变更 | 来源 |
|---|---|---|
| Blogger.audience_profile | +JSONB NULL（灰豚画像） | U11 新增字段（migration 015 ALTER） |
| Blogger.blogger_type | 已存在 VARCHAR(16) | U03；U11 自动写入 |
| Blogger.quality_tags | 已存在 JSONB[] | U03；U11 自动写入 |
| Blogger.is_suspected_fake | 已存在 bool | U03；U11 自动写入 |

> 无新表。唯一 DDL = ALTER blogger ADD audience_profile JSONB。

---

## 2. audience_profile 结构（灰豚同步写入）

```json
{
  "gender_distribution": {"male": 0.3, "female": 0.65, "unknown": 0.05},
  "age_distribution": {"18-24": 0.4, "25-34": 0.35, "35+": 0.25},
  "city_top5": ["上海", "北京", "杭州", "广州", "深圳"],
  "note_stats": {
    "total_notes": 120,
    "avg_reads": 5000,
    "avg_likes": 300,
    "hit_rate": 0.15,
    "active_fans": 8000
  },
  "synced_at": "2026-06-01T12:00:00Z"
}
```
- 由 U13 灰豚 adapter 定期写入；U11 仅读展示。
- null = 灰豚未同步，前端显示"暂无灰豚数据"。

---

## 3. 阈值常量（tag_config.py）

| 常量 | 默认值 | 说明 |
|---|---|---|
| FOLLOWER_KOC_MIN | 10000 | 粉丝 ≥ 此 = KOC |
| FOLLOWER_KOL_MIN | 100000 | 粉丝 ≥ 此 = KOL |
| FAKE_RATIO_THRESHOLD | 0.01 | read_like_ratio ≤ 此 = 疑似假号 |
| HIGH_CPL_THRESHOLD | Decimal("5.00") | 平均 CPL ≤ 此 = 高性价比 |
| HIT_RATE_THRESHOLD | 0.2 | 命中率 ≥ 此 = 带货型 |

---

## 4. 衍生字段（不存 DB，读时计算）

| 字段 | 算法 | 说明 |
|---|---|---|
| read_like_ratio | note_stats.avg_likes / note_stats.avg_reads | 分母 0 → null |

---

## 5. BloggerTagService 算法概要

| 方法 | 输入 | 输出 | 触发 |
|---|---|---|---|
| compute_blogger_type | follower_count | 素人/KOC/KOL | 创建/更新 follower |
| compute_read_like_ratio | audience_profile | Decimal / null | 读时 |
| is_fake_account | read_like_ratio | bool | recompute 任务 |
| compute_quality_tags | 历史推广 CPL + hit_rate | list[str] | recompute 任务 |
| recompute_for_tenant | tenant_id | int（更新数） | Celery 任务 |
