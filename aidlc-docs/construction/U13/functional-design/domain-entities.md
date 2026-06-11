# U13 领域实体（Domain Entities）

> 单元：U13 — 自动数据采集 Worker
> 故事：EP07-S11~S14

---

## 1. 实体概览

| 实体 | 类型 | 用途 |
|---|---|---|
| `WorkerToken` | ORM (TenantScopedModel) | Worker 鉴权令牌（独立于用户 JWT）+ IP allowlist |
| `CrawlerTask` | ORM (TenantScopedModel) | 采集任务队列（pull 模型）+ 一次性 cred_token |
| `DataQualityIssue` | ORM (TenantScopedModel) | 数据质量异常记录（info/warning/error）|
| `QianniuDaily` | ORM (TenantScopedModel) | 千牛商品日报数据（S11 落库目标）|
| `AdDaily` | ORM (TenantScopedModel) | 万相台广告日报数据（S12 落库目标）|

> 灰豚（S13）不新建表——更新 `blogger.audience_profile`（U11 已加 JSONB 列）。
> 3 adapter（qianniu/wanxiangtai/huitun）实现 ImportAdapter 协议，放 `modules/importer/adapters/`。

---

## 2. WorkerToken 实体

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | — | TenantScopedModel | |
| name | VARCHAR(64) | NOT NULL | Worker 标识名（如 "vm-crawler-01"） |
| token_hash | VARCHAR(64) | NOT NULL UNIQUE(tenant) | sha256(明文 token)，明文仅签发时返回一次 |
| ip_allowlist | JSONB | NOT NULL DEFAULT '[]' | 允许的 Worker IP 列表 |
| is_active | BOOL | NOT NULL DEFAULT true | 吊销置 false |
| consecutive_auth_failures | INT | NOT NULL DEFAULT 0 | 连续鉴权失败计数 |
| last_seen_at | TIMESTAMPTZ | NULL | 最近 poll 时间 |

约束：`UNIQUE(tenant_id, token_hash)`、`idx_worker_token_active(tenant_id, is_active)`、RLS。

---

## 3. CrawlerTask 实体

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | — | TenantScopedModel | |
| platform | VARCHAR(16) | NOT NULL | 千牛/万相台/灰豚 |
| credential_id | UUID | FK credential ON DELETE CASCADE | 关联凭据 |
| target_date | DATE | NOT NULL | 采集目标日期（默认昨天） |
| status | VARCHAR(16) | NOT NULL DEFAULT 'pending' | pending/assigned/exchanged/success/failed |
| worker_token_id | UUID | FK worker_token SET NULL，NULL | 领取的 Worker |
| cred_token | VARCHAR(64) | NULL | 一次性凭据交换令牌（exchange 后清空） |
| cred_token_expires_at | TIMESTAMPTZ | NULL | cred_token TTL（默认 5 分钟） |
| assigned_at | TIMESTAMPTZ | NULL | 领取时间 |
| import_batch_id | UUID | NULL | 成功后回填导入批次 |
| error_reason | TEXT | NULL | 失败原因 |
| attempt | INT | NOT NULL DEFAULT 0 | 派发尝试次数 |

约束：`UNIQUE(tenant_id, platform, credential_id, target_date)`（防重复派发）、`idx_crawler_task_status(tenant_id, status)`、`CHECK status IN (...)`、RLS。

### 状态机

```
pending ──poll──▶ assigned ──exchange──▶ exchanged ──result(success)──▶ success
   │                  │                                  │
   │                  └────────────── result(failed) ───┴──▶ failed
   └─ (Beat 重新生成下一日任务)
```

---

## 4. DataQualityIssue 实体

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | — | TenantScopedModel | |
| source | VARCHAR(32) | NOT NULL | qianniu/wanxiangtai/huitun/manual_* |
| severity | VARCHAR(8) | NOT NULL | info/warning/error |
| status | VARCHAR(8) | NOT NULL DEFAULT 'open' | open/fixed/ignored |
| entity_type | VARCHAR(32) | NULL | 关联实体类型（如 platform_product / blogger） |
| entity_ref | VARCHAR(128) | NULL | 关联标识（platform_id / xiaohongshu_id 等） |
| message | TEXT | NOT NULL | 异常描述 |

约束：`CHECK severity IN ('info','warning','error')`、`CHECK status IN ('open','fixed','ignored')`、`idx_dq_tenant_source_sev(tenant_id, source, severity)`、`idx_dq_tenant_status(tenant_id, status)`、RLS。

---

## 5. QianniuDaily 实体（S11 落库）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | — | TenantScopedModel | |
| platform_product_id | UUID | FK platform_product，NULL | 反查命中的映射（未匹配 NULL）|
| platform_id_snapshot | VARCHAR(64) | NOT NULL | 平台商品 ID 原值（未匹配也留存）|
| date | DATE | NOT NULL | 数据日期 |
| visitors | INT | NULL | 访客数 |
| pay_amount | NUMERIC(12,2) | NULL | 支付金额 |
| pay_orders | INT | NULL | 支付订单数 |
| extra | JSONB | NULL | 其他原始指标 |

约束：`UNIQUE(tenant_id, platform_id_snapshot, date)`（幂等，EP07-S11 GWT）、`idx_qianniu_daily_date(tenant_id, date)`、RLS。

---

## 6. AdDaily 实体（S12 落库）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | — | TenantScopedModel | |
| platform_product_id | UUID | FK platform_product，NULL | 反查映射 |
| platform_id_snapshot | VARCHAR(64) | NOT NULL | 广告关联商品 ID 原值 |
| date | DATE | NOT NULL | 数据日期 |
| cost | NUMERIC(12,2) | NULL | 广告花费 |
| impressions | INT | NULL | 曝光 |
| clicks | INT | NULL | 点击 |
| gmv | NUMERIC(12,2) | NULL | 成交额 |
| extra | JSONB | NULL | 其他原始指标 |

约束：`UNIQUE(tenant_id, platform_id_snapshot, date)`、`idx_ad_daily_date(tenant_id, date)`、RLS。

---

## 7. 3 Adapter 映射

| Adapter | source | 目标 | 关键逻辑 |
|---|---|---|---|
| QianniuAdapter | qianniu | qianniu_daily | find_by_platform_id 反查 platform_product → 填 platform_product_id；未匹配 → DataQualityIssue(warning) + platform_product_id=NULL；UNIQUE upsert |
| WanxiangtaiAdapter | wanxiangtai | ad_daily | 同上反查 + UNIQUE upsert |
| HuitunAdapter | huitun | blogger.audience_profile | 按 xiaohongshu_id 匹配 blogger → 更新 audience_profile JSONB；未匹配 → DataQualityIssue(warning) |

> 3 adapter 均实现 ImportAdapter 协议（parse_row / validate / async upsert）；upsert 不自行 commit（runner 控制每行事务）。

---

## 8. cred_token 流转（一次性令牌）

```
schedule_daily_tasks → CrawlerTask(status=pending, cred_token=NULL)
  ↓ Worker poll
poll → 生成 cred_token=token_urlsafe(32) + expires_at=now+5min → status=assigned
  ↓ Worker exchange(cred_token)
exchange → 校验 token 匹配+未过期 → CredentialService.decrypt_for_purpose → 返回明文
         → 清空 cred_token（一次性）→ status=exchanged
  ↓ Worker 登录平台采集 + 上传文件 + result
result(success) → ImportService.upload_for_crawler → import_batch_id 回填 → status=success
result(failed) → CredentialService.report_failure → status=failed
```

---

## 9. ER 关系

```
credential (U12) ──1:N──▶ crawler_task
worker_token   ──1:N──▶ crawler_task (worker_token_id)
crawler_task   ──N:1──▶ import_batch (U06a, import_batch_id)
platform_product (U10b) ──1:N──▶ qianniu_daily / ad_daily
blogger (U03/U11) ◀── huitun adapter 更新 audience_profile
data_quality_issue 独立（entity_ref 弱关联）
```

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| EP07-S11 千牛 → qianniu_daily UNIQUE 幂等 | ✅ §5 |
| EP07-S12 万相台 → ad_daily | ✅ §6 |
| EP07-S13 灰豚 → blogger.audience_profile | ✅ §7 |
| EP07-S14 data_quality 看板 | ✅ §4 |
| §2.2.1 Worker 安全边界（token/IP/cred_token/TTL/审计） | ✅ §2 §8 |
| 复用 U12 decrypt + U10b find_by_platform_id + U06a upload | ✅ §7 §8 |
