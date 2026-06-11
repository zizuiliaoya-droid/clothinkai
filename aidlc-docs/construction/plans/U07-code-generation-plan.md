# U07 代码生成计划（Code Generation Plan）

> 单元：U07 — 企微集成基础
> 节奏：**分 5 批 + Build & Test**（modules/wecom 全量 + crypto 落地 + migration 011 + 测试）
> 依赖：U04（promotion + urge_calculator）+ U01（crypto 占位 / audit / RLS / Celery）+ U03（blogger）

---

## 1. 单元上下文

### 1.1 覆盖故事
EP08-S02 配置 / S03 绑定 / S04 模板 / S05 扫描 / S06 群发 / S07 频控降级 / S08 回调

### 1.2 设计守护（P-U07-01~05）

| 守护 | 落地 |
|---|---|
| P-U07-01 凭据加密落地 | crypto.py AESGCM + 每租户 HKDF；tag 防篡改 |
| P-U07-02 WecomClient + token 缓存 | async httpx + Redis 7000s + 失效重试 |
| P-U07-03 扫描编排 | 逐租户 set_config + 聚合 + 幂等 + execute.delay |
| P-U07-04 群发+频控降级 | 每消息独立事务 + DB 当天计数 + notify |
| P-U07-05 回调签名+幂等 | tenant 路由 + SHA1 + 幂等推进 |

### 1.3 项目结构
```
backend/app/core/security/crypto.py             # 修改：落地 encrypt/decrypt
backend/app/core/config.py                       # 修改：+4 WECOM 变量
backend/app/core/metrics.py                      # 修改：+4 指标
backend/app/core/celery_app.py                   # 修改：autodiscover + beat
backend/app/main.py                              # 修改：注册 3 router + seed 模板
backend/app/modules/auth/default_roles.py        # 修改：pr +wecom/notification 权限
backend/app/modules/promotion/repository.py      # 修改：+find_urge_candidates
backend/app/modules/wecom/                        # 新建 包（19 文件）
backend/alembic/versions/011_u07_create_wecom_tables.py   # 新建
backend/tests/unit/{test_crypto_wecom,test_wecom_domain,test_wecom_message_status}.py
backend/tests/integration/{test_wecom_config,test_wecom_bind,test_wecom_scan,test_wecom_send,test_wecom_callback,test_notification}.py
backend/.env.example                             # 修改：+4 变量
```

---

## 2. 批次划分

### Batch 1 — 基础 + 横切（foundation）
- [x] 1.1 `core/security/crypto.py` 落地 encrypt_credential / decrypt_credential（AESGCM+HKDF）+ CredentialDecryptError
- [x] 1.2 `core/config.py` +WECOM_API_BASE/HTTP_TIMEOUT/TOKEN_TTL/URGE_SCAN_CRON
- [x] 1.3 `core/metrics.py` +4 指标
- [x] 1.4 `modules/wecom/__init__.py` + `enums.py` + `exceptions.py` + `permissions.py`
- [x] 1.5 `default_roles.py` pr +wecom.bind:write/wecom.message:read/notification:read；pr_manager 同

### Batch 2 — 模型 + Schema + 仓储 + 领域
- [x] 2.1 `models.py`（WecomConfig/WecomContact/MessageTemplate/WecomMessage/Notification）
- [x] 2.2 `schemas.py`（secret 不回显）
- [x] 2.3 `repository.py`（5 仓储 + create_message/exists_today_non_failed/count_today/find_by_msgid/get_contact）
- [x] 2.4 `domain.py`（render_template + validate_template_vars + is_important）

### Batch 3 — Client + Services
- [x] 3.1 `client.py`（WecomClient async + WecomCrypto 回调签名/解密）
- [x] 3.2 `config_service.py` + `bind_service.py` + `template_service.py`
- [x] 3.3 `scan_service.py` + `send_service.py` + `callback_service.py` + `notification_service.py`

### Batch 4 — API + Tasks + Wiring
- [x] 4.1 `deps.py` + `api.py` + `callback_api.py` + `notification_api.py`
- [x] 4.2 `app/tasks/wecom_tasks.py`（scan_and_dispatch_urge + execute_wecom_message）
- [x] 4.3 `celery_app.py`（autodiscover + beat）+ `main.py`（注册 router + 公开路径白名单 + seed 模板）
- [x] 4.4 `promotion/repository.py` +find_urge_candidates

### Batch 5 — Migration + Tests + 文档
- [x] 5.1 `011_u07_create_wecom_tables.py`（5 表 + 索引 + RLS + 权限 seed）
- [x] 5.2 3 单元测试 + 6 集成测试 + conftest 扩展（wecom fixtures）
- [x] 5.3 `.env.example` +4 变量
- [x] 5.4 3 文档 `U07/code/{README,api-endpoints,test-coverage}.md`

### Build & Test
- [x] B.1 Docker（PG16:5549 + Redis7:6404 + Py3.12）：alembic upgrade head（011）
- [x] B.2 U07 子集（unit + integration，WecomClient mock）
- [x] B.3 全量回归（576 + U07 新增）+ 覆盖率 ≥70%
- [x] B.4 清理容器/脚本

---

## 3. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP08-S02 | config_service + crypto | test_wecom_config |
| EP08-S03 | bind_service + client.find_external_userid | test_wecom_bind |
| EP08-S04 | template_service + domain.validate_vars | test_wecom_domain + test_wecom_config |
| EP08-S05 | scan_service + tasks.scan + find_urge_candidates | test_wecom_scan |
| EP08-S06 | send_service + client.send + callback created→sent | test_wecom_send + test_wecom_callback |
| EP08-S07 | send_service._degrade + notification_service | test_wecom_send + test_notification |
| EP08-S08 | callback_service + WecomCrypto.verify | test_wecom_callback |

---

## 4. 节奏决策
分 5 批（同 U01/U05）：U07 是 MVP 大单元（新模块 + 凭据加密落地 + 外部集成 + Celery 双任务 + 公开回调）。

---

**等待用户"继续"批准分批节奏；本轮开始 Batch 1。**
