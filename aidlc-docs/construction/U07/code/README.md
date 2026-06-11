# U07 代码生成说明（企微集成基础）

> 单元：U07 — 企微集成基础（EP08-S02~S08）
> 节奏：5 批 + Build & Test
> 依赖：U04（promotion + urge_calculator）+ U01（crypto 占位 / audit / RLS / Celery）+ U03（blogger）

---

## 1. 交付物清单

### 新建 — modules/wecom/（19 文件）
__init__ / enums / exceptions / permissions / models / schemas / repository / domain /
client / config_service / bind_service / template_service / scan_service / send_service /
callback_service / notification_service / deps / api / callback_api / notification_api

### 新建 — 其他
- `app/tasks/wecom_tasks.py`（scan_and_dispatch_urge + execute_wecom_message）
- `alembic/versions/011_u07_create_wecom_tables.py`（5 表 + 索引 + RLS + 权限 seed）
- 3 单元测试 + 6 集成测试

### 修改 — 横切
- `core/security/crypto.py`（落地 AESGCM+HKDF）
- `core/config.py`（+4 WECOM 变量）
- `core/metrics.py`（+4 指标）
- `core/celery_app.py`（autodiscover wecom_tasks + beat wecom-urge-scan）
- `main.py`（注册 3 router）
- `modules/auth/default_roles.py`（pr/pr_manager/operations/finance/merchandiser +权限）
- `modules/promotion/repository.py`（+find_urge_candidates）
- `tests/conftest.py`（+import wecom.models）
- `.env.example`（+4 变量）

---

## 2. 设计守护落地（P-U07-01~05）

| 守护 | 落地 |
|---|---|
| P-U07-01 凭据加密 | crypto.py AES-256-GCM + 每租户 HKDF；tag 防篡改 → CredentialDecryptError |
| P-U07-02 WecomClient + token 缓存 | async httpx + Redis 7000s + 40014/42001 刷新重试 + 错误码映射 |
| P-U07-03 扫描编排 | bypass 读 active 租户 → 逐租户 set_config → find_urge_candidates → 聚合 → 幂等 → delay |
| P-U07-04 群发+频控降级 | 每消息独立事务 + DB 当天计数 → rate_limited + notify |
| P-U07-05 回调签名+幂等 | tenant 路由 + SHA1 + AES + 仅 created→sent/rejected/failed |

---

## 3. 关键语义

- **凭据安全**：secret AES-256-GCM 加密落库（bytea），响应仅 `secret_configured: bool`；解密走 system_context + audit。
- **频控**：每博主每天 1 条 + 每 PR 每天 1 次（DB `wecom_message` 当天 status∈{created,sent} 计数）；命中 → rate_limited + 站内通知。
- **回调**：公开端点 `/api/wecom/callback/{tenant_id}`（无 JWT，签名校验），幂等忽略未知/已处理 msgid。
- **模板**：4 变量白名单（{博主昵称}{商品简称}{预定发布日期}{剩余天数}），缺失回退默认（不在 lifespan seed）。
- **不改 blogger 表**：独立 wecom_contact 表。

---

## 4. 故事覆盖

| 故事 | 实施 |
|---|---|
| EP08-S02 配置 | config_service + crypto + api PUT/GET/test |
| EP08-S03 绑定 | bind_service + client.find_external_userid |
| EP08-S04 模板 | template_service + domain.validate_vars |
| EP08-S05 扫描 | scan_service + wecom_tasks + find_urge_candidates |
| EP08-S06 群发 | send_service + client.send + callback created→sent |
| EP08-S07 频控降级 | send_service._degrade + notification_service |
| EP08-S08 回调 | callback_service + WecomCrypto |

---

## 5. 验证
- 全部新文件诊断器无警告。
- Build & Test：见 test-coverage.md。
