# U11 业务规则（博主智能标签 + 灰豚展示）

> 单元：U11 — EP04-S04~S08

---

## 1. blogger_type 自动计算（BR-U11-01~04，S04）

- **BR-U11-01**：follower_count < FOLLOWER_KOC_MIN → 素人。
- **BR-U11-02**：FOLLOWER_KOC_MIN ≤ follower < FOLLOWER_KOL_MIN → KOC。
- **BR-U11-03**：follower ≥ FOLLOWER_KOL_MIN → KOL。
- **BR-U11-04**：follower_count 为 null → blogger_type 置 null（不判定）。

触发：blogger 创建/更新 follower_count 时 BloggerTagService.compute_and_save_type 同步执行。

## 2. read_like_ratio（BR-U11-10~12，S05）

- **BR-U11-10**：ratio = note_stats.avg_likes / note_stats.avg_reads。
- **BR-U11-11**：avg_reads = 0 → ratio = null，前端显示"—"。
- **BR-U11-12**：audience_profile = null（灰豚未同步）→ ratio = null。

衍生字段，读时计算，不存 DB。

## 3. 假号判断（BR-U11-20~22，S06）

- **BR-U11-20**：read_like_ratio ≤ FAKE_RATIO_THRESHOLD → is_suspected_fake = true。
- **BR-U11-21**：ratio = null（无数据）→ is_suspected_fake 保持当前值不更新。
- **BR-U11-22**：阈值调整后重新计算全部博主（recompute 任务）。

## 4. 质量标签（BR-U11-30~33，S07）

- **BR-U11-30**：高性价比 = 博主历史推广平均 CPL ≤ HIGH_CPL_THRESHOLD。
  - CPL = AVG(promotion.quote_amount) / SUM(effective_like_count) per blogger；无推广 → 不打此标签。
- **BR-U11-31**：带货型 = 博主推广命中率 ≥ HIT_RATE_THRESHOLD。
  - 命中率 = COUNT(is_hit=true) / COUNT(*)；无推广 → 不打。
- **BR-U11-32**：多标签并存（quality_tags 数组 JSONB）。
- **BR-U11-33**：recompute 任务：逐 blogger 重算 → UPDATE quality_tags + is_suspected_fake。

## 5. audience_profile 展示（BR-U11-40~41，S08）

- **BR-U11-40**：GET blogger detail 返回 audience_profile 完整 JSON。
- **BR-U11-41**：audience_profile = null → 前端显示"暂无灰豚数据"（API 返回 null）。

## 6. 批量重算（BR-U11-50~52）

- **BR-U11-50**：Celery 任务 `recompute_all_blogger_tags`（逐 tenant → 逐 active blogger）。
- **BR-U11-51**：触发方式：admin 手动（POST /api/bloggers/recompute-tags）或 Beat 每日 02:00。
- **BR-U11-52**：返回更新数量；非阻塞（异步任务）。

## 7. 权限（BR-U11-60）

- recompute 端点鉴权 admin（* 通配）。
- 标签计算为内部组件（无单独 scope），读随 blogger.*:read。

## 8. 错误码

| 场景 | 行为 |
|---|---|
| follower_count null | blogger_type = null（不报错） |
| ratio null | null（不报错） |
| 无推广记录 | quality_tags 不打（空数组，不报错） |
