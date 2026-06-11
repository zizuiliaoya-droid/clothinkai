# U01 业务规则（Business Rules）

> 基于 14 个决策问题答案 + requirements.md 第 3.3、3.4、11.1、12.4 节的具体业务规则。

---

## 1. 密码策略（Q1=B 中等）

### BR-PWD-001 密码强度
密码必须满足：
- **长度** ≥ 10 字符
- 至少包含 **1 个大写字母**
- 至少包含 **1 个小写字母**
- 至少包含 **1 个数字**

不强制要求特殊字符（用户可选）。

### BR-PWD-002 密码哈希
使用 **bcrypt**（cost factor 12），不存明文，不存可逆加密。

### BR-PWD-003 密码不可与历史密码相同
本阶段（U01）**不实施**密码历史校验。预留扩展点，未来可加 `password_history` 表。

### BR-PWD-004 临时密码
管理员创建用户或重置密码时：
- 系统生成 16 位随机密码（大小写 + 数字 + 1 个特殊字符）
- 一次性返回给管理员（明文，但不写入任何日志）
- 用户表 `password_must_change=true`

---

## 2. 登录失败处理（Q2=C 双层）

### BR-AUTH-001 (IP, username) 维度限流
- 每个 **(IP, username)** 组合连续 5 次登录失败 → 限流 **15 分钟**（429 响应）
- 限流计数存 Redis：键 `login:fail:{ip}:{username}`，过期时间 15 分钟，每次失败 +1
- 成功登录后清除该键
- **实施位置**：`AuthService.login` 内部用 Redis incr/expire（不在 slowapi 中），因为 slowapi key_func 是同步函数，无法 await `request.json()`，且读 body 会破坏 Pydantic 解析。详见 `nfr-design/nfr-design-patterns.md` 第 3 节
- **slowapi 在登录端点上**只承担 IP 级限流（默认 100/min 全局 + 登录端点 20/min/IP），不感知 username

### BR-AUTH-002 账户级锁定
- 用户表 `failed_login_count` 累计（不受 Redis 重置影响，仅成功登录或管理员解锁时清零）
- 累计 **10 次失败** → 设置 `locked_at = NOW()`，需管理员手动解锁
- 已锁定账户：登录返回 423 Locked，告知"请联系管理员"
- 管理员解锁：调用 `PUT /api/users/{id}/unlock` → 清空 `locked_at` + `failed_login_count`

### BR-AUTH-003 失败审计
每次登录失败写 audit_log：
- `action = "login_failed"`
- `actor_type = "user"`（如果用户存在）或 `"unknown"`（用户不存在时也记录尝试）
- `ip` + `user_agent` 字段填充

---

## 3. JWT Token 生命周期（Q3=A 标准）

### BR-TKN-001 access_token
- 有效期：**30 分钟**
- 无状态校验（不存库）
- Payload：`{ sub: user_id, tenant_id, roles: [...], exp, iat, jti, pwd_iat: password_changed_at }`
- 算法：HS256（HMAC-SHA256），密钥从环境变量读取

### BR-TKN-002 refresh_token
- 有效期：**7 天**
- 存库 `refresh_token` 表（jti / expires_at / revoked_at）
- 滑动续期：每次 refresh 时签发新 access + 新 refresh，旧 refresh 标记 `revoked_at`

### BR-TKN-003 Token 校验流程
```
校验 access_token:
1. 验签
2. 检查 exp 未过期
3. 取 user_id + tenant_id 加载用户
4. 校验 user.status == 'active' AND user.deleted_at IS NULL
5. 校验 user.locked_at IS NULL
6. 校验 user.tenant_id == token.tenant_id
7. 校验 token.pwd_iat == user.password_changed_at（防止密码修改后老 token 仍可用）
任一失败 → 401，强制重新登录
```

### BR-TKN-004 Token 失效场景（Q4=C 全场景）
以下任一变更立即吊销该用户所有 refresh_token + 使所有现有 access_token 失效：
- 密码修改
- 用户被禁用（status=disabled）
- 用户被锁定（locked_at 非空）
- 角色变更（user_role 增/减）
- 用户级权限变更（user_permission_override 任一变化）
- 用户被软删除

**实施手段**：
- access_token：通过 `pwd_iat` 字段 + 触发更新 `password_changed_at`（即使不是密码改动也更新这个字段，作为"安全戳"）
- refresh_token：批量更新 revoked_at = NOW()

---

## 4. 首次登录强制改密（Q5=A 严格）

### BR-PWD-005 强制改密
- 用户 `password_must_change=true` 时：
  - 登录成功，签发 token，但 token 中带标记 `must_change_password=true`
  - 除以下接口外，所有 API 返回 **423 Locked**：
    - `GET /api/auth/me`（让前端知道当前用户）
    - `PUT /api/auth/password`（修改密码）
    - `POST /api/auth/logout`
  - 修改密码成功后 `password_must_change=false`，原 token 失效，需重新登录

---

## 5. 权限合并算法（Q6=C 撤销 > 授予 > 角色）

### BR-PERM-001 有效权限计算
对用户 U 的有效权限集 `effective(U)`：

```
roles_perms = ⋃ {role_permission for role in user.roles}
overrides = user_permission_override for user
grants = {p for o in overrides if o.effect = 'grant'}
revokes = {p for o in overrides if o.effect = 'revoke'}

effective(U) = (roles_perms ∪ grants) - revokes
```

**优先级**：`revoke` 始终最高 — 即使该 permission 同时在 `roles_perms` 和 `grants` 中，只要存在 revoke override 就被排除。

### BR-PERM-002 权限缓存
- 每用户的 effective_permissions 缓存到 Redis：键 `perm:user:{user_id}`，TTL 10 分钟
- 用户/角色/override 任一变化时立即清除该用户缓存

### BR-PERM-003 字段级权限（U09 启用）
U09 阶段以同样的 `permission` + `user_permission_override` 表存储字段级权限：
- `permission.scope` 用 `<entity>.<field>` 格式（Q13=A），如 `sku.cost_price`
- 默认情况下"敏感字段"在 role_permission 中按角色显式授予；非敏感字段所有人可读，无需 permission 记录
- 敏感字段清单（U01 阶段定义占位，U09 实施）：
  - `sku.cost_price`、`sku.purchase_price`
  - `blogger.quote`、`blogger.wechat_id`
  - `promotion.quote_amount`
  - `settlement.payment_amount`
  - `credential.username`（凭据账号也属敏感）

---

## 6. 多租户上下文规则（Q7=D 组合）

### BR-TENANCY-001 tenant_id 注入
- FastAPI 中间件 `tenancy_middleware` 在请求开始时从 JWT 中读取 `tenant_id`
- 写入 SQLAlchemy Session 的 `info["tenant_id"]`
- 请求结束时清除

### BR-TENANCY-002 ORM 自动过滤
- SQLAlchemy `before_compile` 事件：所有继承 `TenantScopedModel` 基类的查询自动注入 `WHERE tenant_id = :info_tenant_id`
- 写入操作（INSERT/UPDATE）：如果 `info` 中有 tenant_id 但模型字段未提供，自动填入；如果两者冲突 → 抛 TenantContextMismatch 异常

### BR-TENANCY-003 缺失上下文处理
查询 `TenantScopedModel` 但 Session info 中**没有** tenant_id 时：
- 默认行为：抛 `TenantContextMissing` 异常 → 返回 500
- **唯一允许的例外**：
  1. **system_context()** 上下文管理器：明确标记跨租户的系统任务（备份、平台级管理任务、Celery Beat 调度器）
     ```python
     with system_context(session):
         # 跨租户查询允许
         all_tenants = session.query(Tenant).all()
     ```
  2. **platform_admin token**：JWT payload 中 `actor_type = "platform_admin"` 时，中间件不注入 tenant_id，所有查询绕过过滤（但记录详细 audit_log）

### BR-TENANCY-004 PostgreSQL RLS 兜底
- 核心表（user, refresh_token, audit_log, 业务表）启用 RLS
- 策略：`USING (tenant_id = current_setting('app.tenant_id')::uuid OR current_setting('app.bypass_rls', true) = 'on')`
- ORM 中通过 `SET LOCAL app.tenant_id = '...'` 在每个事务开始时设置
- system_context / platform_admin 时设置 `SET LOCAL app.bypass_rls = 'on'`

---

## 7. 审计日志规则（Q8=B 1年DB+归档）

### BR-AUDIT-001 必记录的操作
| 操作 | action | 触发位置 |
|---|---|---|
| 用户登录成功 | `login` | AuthService.login |
| 用户登录失败 | `login_failed` | AuthService.login（含未知用户名） |
| 密码修改 | `password_change` | AuthService.change_password |
| 用户创建 | `user_create` | UserService.create |
| 用户禁用/启用 | `user_toggle` | UserService.toggle_active |
| 用户锁定/解锁 | `user_lock` / `user_unlock` | AuthService / UserService |
| 角色分配 | `role_assign` / `role_revoke` | UserService.assign_roles |
| 权限授予/撤销 | `perm_grant` / `perm_revoke` | PermissionService |
| 凭据解密 | `decrypt` | core/security/crypto（U12 阶段） |
| 平台管理员跨租户访问 | `platform_admin_access` | tenancy_middleware |

### BR-AUDIT-002 不可篡改
- DB 层 REVOKE UPDATE/DELETE
- ORM 层 audit_log 模型禁用 update / delete 方法（抛异常）
- 仅允许 INSERT 和 SELECT

### BR-AUDIT-003 归档
- Celery Beat 每月 1 日 04:00 执行归档任务：
  - 查询超过 1 年的 audit_log 记录
  - 序列化为 JSONL（每行一条）gzip 压缩
  - 上传到 R2 `backups/audit-archive/{tenant_id}/{YYYY-MM}.jsonl.gz`
  - 校验上传成功（HEAD + 比对 size）
  - 通过专门的 `archiver_role` DB role 执行 DELETE（绕过 REVOKE）
  - 归档操作本身写入 audit_log（`action="audit_archive"`）

---

## 8. 备份与恢复规则（Q9=C, Q10=B）

### BR-BACKUP-001 每日备份范围
Celery Beat 每天 03:00 触发，备份内容（按顺序）：
1. **PostgreSQL 全库 pg_dump**（含所有租户数据）→ `pg-{YYYY-MM-DD}.sql.gz`
2. **R2 凭据桶清单与对象**复制 → `credentials-{YYYY-MM-DD}.tar.gz`（仅清单和元数据，密文已经存在 R2 不重复）
3. **关键配置导出**（JSON）：
   - field_mapping 全部版本
   - message_template
   - role_permission 矩阵
   - 系统设置（阈值配置）
   - → `config-{YYYY-MM-DD}.json.gz`
4. 三个文件合并为 `daily-{YYYY-MM-DD}.tar.gz`，上传到 R2 `backups/daily/`
5. 计算 SHA256 checksum，写入 `backup_record`
6. 任一步骤失败 → backup_record.status='failed' + 写日志（U07 完成后增强：通过企微告警管理员）

### BR-BACKUP-002 保留策略
- **每日备份**：保留 30 天，超过自动删除
- **每月备份**：每月 1 日的备份升级为 monthly（retention_until = 1 年后）
- **每月备份**：保留 1 年，超过自动删除
- 清理任务每天 04:00 执行

### BR-BACKUP-003 恢复演练（Q10=B 半自动）
提供 `backend/scripts/restore_backup.py`：
- 输入：`--backup-id <UUID>` 或 `--date YYYY-MM-DD`
- 输出：恢复到指定 staging 数据库 + 校验 R2 备份完整性 + 跑 smoke test
- 验收清单（每季度执行）：
  1. 选取最近一份 daily 备份
  2. 在隔离环境跑 restore_backup.py
  3. 验证用户能登录
  4. 验证至少 1 条样例推广可查询
  5. 写入 backup_record（type=restore_drill, status=success）

### BR-BACKUP-004 RPO / RTO
- RPO ≤ 24 小时（每日备份）
- RTO ≤ 4 小时（脚本 + 验证清单）

---

## 9. 系统初始化规则（Q11=C Alembic seed + 启动检查）

### BR-INIT-001 默认数据 seed
通过 Alembic data migration（`alembic/versions/001_seed_initial_data.py`）写入：
- 1 个默认 tenant（code='default', name='默认租户'）
- 10 个预设 role（按 default_roles.py 常量）
- 所有内置 permission（按代码常量清单）
- role_permission 关联（按 default_roles.py 中的角色权限矩阵）

### BR-INIT-002 启动管理员检查
backend 应用启动时执行：
```
if 默认租户中没有管理员账号:
    生成 16 位随机密码
    创建 username='admin', role='admin', password_must_change=true
    打印明文密码到 stdout（仅首次启动）
    写入 audit_log（action="initial_admin_created"，password 字段不存）
```

### BR-INIT-003 Platform Admin
platform_admin 角色不属于任何租户。通过单独的 CLI 命令创建：
```
python -m app.cli create-platform-admin --username <name>
```
该用户的 JWT 中 `tenant_id=NULL` + `actor_type="platform_admin"`，中间件特殊处理。

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 14 个决策全部转化为可执行业务规则 | ✅ |
| 与 requirements.md 第 3.3 安全 NFR 一致 | ✅（JWT/bcrypt/限流/审计） |
| 与 requirements.md 第 3.4 多租户一致 | ✅（共享 DB + tenant_id + RLS） |
| 与 requirements.md 第 11.1 唯一性约束一致 | ✅（user / refresh_token 等带 tenant_id） |
| 与 requirements.md 第 12.4 解密审计一致 | ✅（BR-AUDIT-001 含 decrypt） |
| 与 requirements.md 第 13.5 权限拒绝验收一致 | ✅（BR-PERM-001 计算 + 缓存） |
| 与 application-design 的 component-methods.md 第 6 节方法一致 | ✅ |
