# U07 基础设施设计（Infrastructure Design）

> 单元：U07 — 企微集成基础
> 原则：复用 U01 既有 Zeabur 拓扑（6 服务），**无新增服务**；增量 = 公开回调路径 + 4 环境变量 + Beat 调度 + migration 011 + 出站企微 HTTPS

---

## 1. 服务拓扑（复用，无新增）

| 服务 | U07 涉及 | 说明 |
|---|---|---|
| backend | ✅ | 配置/绑定/test/模板/消息查询 API + 公开回调端点 + 通知 API |
| celery-worker | ✅ | execute_wecom_message（群发执行 + 频控降级） |
| celery-beat | ✅ | wecom-urge-scan（09:00 催发扫描调度） |
| postgres | ✅ | 5 新表（migration 011） |
| redis | ✅ | access_token 缓存（db=0） |
| frontend | （间接） | 配置/绑定/通知 UI（Code Generation 视情补最小页面） |

> U07 不引入新 Zeabur 服务、不新增 R2 桶。

---

## 2. 公开回调端点可达性

- 路径：`/api/wecom/callback/{tenant_id}`（GET 验证 + POST 接收），**无 JWT**。
- 中间件白名单：在 Tenancy/Auth 中间件加入公开路径前缀 `/api/wecom/callback`（跳过 JWT 校验），靠 **msg_signature SHA1 + AES 解密** 防护（NFR §4.2）。
- 企微后台配置：接收消息 URL = `https://api.<prod-domain>/api/wecom/callback/<tenant_id>`，Token + EncodingAESKey 与 `wecom_config.callback_token` / `callback_aes_key` 一致。
- TLS：复用 Zeabur 自动 Let's Encrypt（api 子域）；企微要求 HTTPS。

---

## 3. 出站企微 HTTPS

- 目标：`https://qyapi.weixin.qq.com`（`WECOM_API_BASE` 可配）。
- 路径：celery-worker（群发/绑定 token）+ backend（test_connection/绑定）→ httpx HTTPS，超时 10s。
- 香港节点直连，无需代理。

---

## 4. 环境变量映射

| 变量 | backend | worker | beat | 默认 | 说明 |
|---|---|---|---|---|---|
| WECOM_API_BASE | ✅ | ✅ | — | https://qyapi.weixin.qq.com | 企微 API 域名 |
| WECOM_HTTP_TIMEOUT | ✅ | ✅ | — | 10 | 外部调用超时（秒） |
| WECOM_TOKEN_TTL | ✅ | ✅ | — | 7000 | access_token 缓存 TTL |
| WECOM_URGE_SCAN_CRON | — | — | ✅ | 0 9 * * * | 催发扫描调度（Asia/Shanghai） |
| CREDENTIAL_MASTER_KEY | ✅ | ✅ | — | （已有 Secret） | AES master key（每租户 HKDF 派生） |

> 均有默认值；未配置 wecom_config 的租户在扫描时跳过，不影响系统启动。

---

## 5. Beat 调度（celery-beat 追加）

| 任务 | 调度 | 队列 |
|---|---|---|
| backup-database-daily | 03:00 | backup |
| cleanup-expired-backups-daily | 04:00 | backup |
| cleanup-expired-refresh-tokens-daily | 04:30 | default |
| **wecom-urge-scan（新增）** | **09:00** | **default** |

错峰设计：催发扫描 09:00 与备份/清理（凌晨）无资源争用。

---

## 6. Redis 用量

- 新增 key 模式：`wecom:token:{tenant_id}`（String，TTL 7000s，每租户 1 个）。
- 量级：租户数级别（极小），不影响 256MB 容量规划（db=0 cache）。

---

## 7. 数据库迁移

- `011_u07_create_wecom_tables`：5 表（wecom_config / wecom_contact / message_template / wecom_message / notification）+ 索引（频控复合 + 通知 + 回调反查）+ UNIQUE + 3 RLS enable + 权限 seed（5 权限点 + 角色映射：管理员全量 / PR = bind:write + message:read + notification:read）。
- 接 010 head；经 U01 既有 migrate job（手动触发）`alembic upgrade head` 部署。

---

## 8. Secrets

- 复用 `CREDENTIAL_MASTER_KEY`（Zeabur Secrets，base64 32B），生产/staging 独立。
- wecom secret 不入 Secrets：由租户在系统内 `PUT /api/settings/wecom` 录入，AES-256-GCM 加密存 DB。

---

## 9. CI/CD 影响

- ci.yml：U07 单元/集成测试纳入既有 pytest job（WecomClient mock，无需企微凭据）。
- 可选新增 `validate-wecom` job（与 U06a validate-import-framework 同模式，校验回调路径白名单 + crypto round-trip）—— Code Generation 阶段视情添加。

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新增 Zeabur 服务 | ✅ §1 |
| 公开回调路径白名单 + 签名防护 | ✅ §2 |
| 4 环境变量三服务分布 + 默认值 | ✅ §4 |
| Beat 调度错峰 | ✅ §5 |
| migration 011 接 010 + RLS + 权限 seed | ✅ §7 |
| 复用 CREDENTIAL_MASTER_KEY 无新 Secret | ✅ §8 |

> 注：本文件经 Kiro spec-format 检测可能报「Missing ## Overview/## Architecture/...」= 已知假阳性（AI-DLC 格式 ≠ Kiro 模板），IGNORE。
