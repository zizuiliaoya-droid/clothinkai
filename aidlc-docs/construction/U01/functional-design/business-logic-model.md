# U01 业务逻辑模型（Business Logic Model）

> 描述 U01 单元的核心业务流程，按用例展开。技术细节（具体加密实现、ORM 钩子代码）属于 NFR Design 和 Code Generation。

---

## 1. 用例：用户登录（EP01-S01）

### 1.1 主路径

```
[输入] username, password, ip, user_agent
    ↓
1. 检查 (ip, username) Redis 限流键
   - 若计数 ≥ 5 → 返回 429 + 写 audit_log("login_rate_limited")
    ↓
2. 按 (tenant_default, username) 查找用户
   - 多租户场景：登录时如何确定 tenant？详见 §1.4
    ↓
3. 验证密码（bcrypt.verify）
   - 失败 → §1.2
    ↓
4. 校验账户状态
   - user.deleted_at != NULL → 401
   - user.status = 'disabled' → 423 Locked，audit_log("login_disabled")
   - user.locked_at != NULL → 423 Locked，audit_log("login_locked")
    ↓
5. 校验租户状态
   - tenant.status = 'suspended' → 403
    ↓
6. 重置失败计数
   - 清除 Redis (ip, username) 计数
   - user.failed_login_count = 0
   - user.last_login_at = NOW()
    ↓
7. 签发 access_token + refresh_token
   - access payload: { sub, tenant_id, roles, jti, iat, exp, pwd_iat: user.password_changed_at, must_change_password }
   - refresh_token 写入 refresh_token 表
    ↓
8. 写 audit_log("login")
    ↓
[输出] { access_token, refresh_token, must_change_password, user_summary }
```

### 1.2 失败分支

```
密码错误：
    ↓
A. Redis (ip, username) 计数 +1，TTL 15 分钟
B. user.failed_login_count += 1
C. 如果 user.failed_login_count >= 10:
       user.locked_at = NOW()
       audit_log("user_lock", reason="exceeded_login_attempts")
D. 写 audit_log("login_failed", { ip, user_agent })
E. 返回 401（不区分用户不存在/密码错误的提示，避免用户名探测）
```

### 1.3 用户不存在分支

```
A. 仍按 (ip, username) 走 Redis incr（与 1.2 一致）
   说明：用户不存在的尝试也计入 (ip, username) 计数，避免攻击者通过尝试不存在的
        用户名绕过限流；返回的 401 错误信息不区分"用户不存在"和"密码错误"
B. 不更新任何 user 字段
C. audit_log("login_failed", actor_type="unknown", attempted_username=...)
D. 返回 401（与"密码错"同样响应）
```

> 全局 IP 限流由 slowapi 默认 100 req/min + 登录端点 20 req/min/IP 兜底；详见 nfr-design 第 3 节。

### 1.4 多租户场景下的 tenant 确定

| 场景 | 策略 |
|---|---|
| 单租户部署（首次） | 默认租户 'default' |
| 多租户部署 | 用户在登录页输入 `tenant_code`（或通过子域名 `<code>.app.clothinkai.com` 解析） |
| Platform Admin | 走专用登录端点 `POST /api/auth/platform-login`，无 tenant_id |

U01 实施单租户登录（按默认 'default' 租户）；多租户登录界面在管理员部署多租户时再扩展（不增加新接口，扩展 `POST /api/auth/login` 加可选 `tenant_code`）。

---

## 2. 用例：修改密码（EP01-S02）

```
[输入] old_password, new_password
[前置] 已登录用户
    ↓
1. 校验 new_password 满足 BR-PWD-001
2. bcrypt.verify(old_password, user.password_hash)
   - 失败 → 401，audit_log("password_change_failed")
3. 检查 new_password != old_password（避免无意义改）
4. 生成新 password_hash
5. 在事务内：
   a. 更新 user.password_hash, user.password_changed_at = NOW(), user.password_must_change = false
   b. 标记当前用户所有 refresh_token 的 revoked_at = NOW()
   c. 清除 Redis perm 缓存
   d. 写 audit_log("password_change")
6. 返回 200，前端清除本地 token，跳转登录
```

---

## 3. 用例：管理员管理用户（EP01-S03）

### 3.1 创建用户

```
[输入] { username, display_name, email, role_codes }
[权限] 调用方需有 auth.user:write 权限
    ↓
1. 校验 username 在租户内唯一
2. 校验 role_codes 都是合法的预设角色
3. 生成 16 位随机密码（满足 BR-PWD-001 + 1 个特殊字符）
4. 在事务内：
   a. 创建 user 记录（password_hash, password_must_change=true, status=active）
   b. 创建 user_role 关联
   c. 写 audit_log("user_create", before=NULL, after={username, role_codes})
5. 返回 (user_summary, plain_password) — plain_password 仅一次性返回，不写日志
```

### 3.2 启用/禁用用户

```
[输入] user_id
[权限] auth.user:write
    ↓
1. 加载用户
2. 翻转 user.status（active ↔ disabled）
3. 在事务内：
   a. 更新 user.status, user.password_changed_at = NOW()  ← 触发 token 失效
   b. revoke 该用户所有 refresh_token
   c. 清除 Redis perm 缓存
   d. 写 audit_log("user_toggle", before, after)
4. 返回更新后的 user
```

### 3.3 解锁用户

```
[输入] user_id
[权限] auth.user:write
    ↓
1. 校验 user.locked_at != NULL（否则返回 422）
2. 在事务内：
   a. user.locked_at = NULL, user.failed_login_count = 0
   b. user.password_changed_at = NOW()  ← 触发 token 失效（防止锁定期间签发的 token）
   c. revoke 所有 refresh_token
   d. 写 audit_log("user_unlock")
3. 返回 user
```

---

## 4. 用例：分配预设角色（EP01-S04）

```
[输入] user_id, role_codes (List[str])
[权限] auth.user:write
    ↓
1. 加载 user + 现有 user_role
2. 校验所有 role_codes 都存在
3. 计算 diff：to_add, to_remove
4. 在事务内：
   a. 删除 to_remove 对应的 user_role
   b. 插入 to_add 对应的 user_role
   c. user.password_changed_at = NOW()  ← 触发 token 失效
   d. revoke 所有 refresh_token
   e. 清除 Redis perm 缓存
   f. 写 audit_log("role_assign", before=旧 roles, after=新 roles)
5. 返回 user_with_roles
```

---

## 5. 用例：多租户隔离（EP01-S07）

### 5.1 ORM Session 注入流程

```
HTTP 请求进入
    ↓
1. tenancy_middleware:
   a. 从 JWT 取 tenant_id, actor_type
   b. 创建 Session
   c. session.info["tenant_id"] = tenant_id
   d. session.info["actor_type"] = actor_type
   e. session.execute("SET LOCAL app.tenant_id = :tid", {"tid": tenant_id})
   f. 如果 actor_type == "platform_admin":
        session.execute("SET LOCAL app.bypass_rls = 'on'")
        audit_log("platform_admin_access", action_detail=request.path)
    ↓
2. 进入 Router → Service → Repository
   - 所有 TenantScopedModel 查询自动 WHERE tenant_id 注入（before_compile 事件）
   - INSERT 时自动填充 tenant_id（before_insert 事件）
   - UPDATE 时校验 model.tenant_id == session.info["tenant_id"]
    ↓
3. 请求结束: session.close()
```

### 5.2 跨租户场景

| 场景 | 实施 |
|---|---|
| 备份任务（pg_dump） | 直接调用 PostgreSQL 客户端，绕过 ORM |
| 平台管理任务（Celery） | 用 `system_context()` 上下文管理器 |
| 平台管理员 API | JWT actor_type='platform_admin'，中间件特殊处理 |

```python
# 系统任务示例（伪代码）
@celery_app.task
def cleanup_expired_refresh_tokens():
    with session_scope() as session:
        with system_context(session):
            # 跨租户查询过期 refresh_token
            session.query(RefreshToken).filter(
                RefreshToken.expires_at < datetime.utcnow()
            ).delete()
```

### 5.3 跨租户访问的 PostgreSQL RLS 校验
即使 ORM 注入失败，PostgreSQL 层也会拦截：

```sql
CREATE POLICY tenant_isolation ON user
    FOR ALL
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    );
```

---

## 6. 用例：审计日志查询（EP01-S08）

```
[输入] action?, resource?, user_id?, date_from?, date_to?, page, page_size
[权限] auth.audit:read（默认仅 admin）
    ↓
1. 构造查询：
   - WHERE tenant_id = current
   - AND action = :action（如有）
   - AND resource = :resource（如有）
   - AND user_id = :user_id（如有）
   - AND created_at BETWEEN :from AND :to
2. ORDER BY created_at DESC
3. 分页（page * page_size）
4. 返回 Page<AuditLogEntry>
```

特殊：
- 平台管理员可查所有租户的 audit_log（中间件已设置 bypass_rls）
- 查询 audit_log 本身**不写**新的 audit_log（避免噪音）

---

## 7. 用例：备份执行流程（EP10-NFR04）

### 7.1 每日备份

```
Celery Beat 每天 03:00 触发 backup_database()
    ↓
1. 创建 backup_record（type=daily, status=running, started_at=NOW()）
    ↓
2. 步骤 A — pg_dump:
   - 执行 pg_dump --format=custom --file=/tmp/pg-DATE.sql.gz
   - 失败 → backup_record.status=failed, error_message, return
    ↓
3. 步骤 B — 凭据桶元数据导出:
   - 通过 R2 SDK 列出 credentials/ 桶下所有对象的 metadata（包含 etag, size, last_modified）
   - 序列化为 JSON: /tmp/credentials-DATE.json
    ↓
4. 步骤 C — 配置导出:
   - 跨租户查询：with system_context()
     SELECT * FROM field_mapping
     SELECT * FROM message_template
     SELECT * FROM role_permission（含关联 role / permission 名称）
     SELECT * FROM system_setting
   - 序列化为 JSON: /tmp/config-DATE.json
    ↓
5. 三个文件合并 + gzip + 计算 SHA256
   /tmp/daily-DATE.tar.gz
    ↓
6. 上传到 R2: backups/daily/daily-DATE.tar.gz
    ↓
7. backup_record:
   - status = success
   - completed_at = NOW()
   - r2_key = ...
   - size_bytes = ...
   - checksum = ...
   - retention_until = today + 30 天（或 today + 1 年 如果是月度首日）
    ↓
8. 清理 /tmp/* 文件
    ↓
9. 触发清理任务: cleanup_expired_backups
    ↓
[结束]
```

### 7.2 备份失败处理
- backup_record.status=failed + error_message
- 写日志 ERROR 级别
- U07 完成后增强：调用 WecomClient.push_to_app(管理员, "备份失败...")

### 7.3 清理过期备份
```
cleanup_expired_backups()
    ↓
SELECT FROM backup_record WHERE retention_until < TODAY AND r2_key IS NOT NULL
FOR each:
    R2 DELETE r2_key
    DELETE FROM backup_record WHERE id = ...
```

### 7.4 月度备份升级
```
每月 1 日的 daily 备份完成后:
    UPDATE backup_record SET 
        backup_type = 'monthly',
        retention_until = today + 365 days
    WHERE id = current
```

### 7.5 恢复演练流程（每季度，半自动）

```bash
# 在 staging 环境执行
python backend/scripts/restore_backup.py --date 2026-05-23 --target staging
```

脚本步骤：
1. 从 R2 下载指定日期的 `daily-DATE.tar.gz`
2. 校验 SHA256
3. 解压三个文件
4. 在 staging 数据库执行 pg_restore
5. 写一个临时 `backup_record(type='restore_drill', status='running')`
6. 跑 smoke test（在脚本中以子进程调用）：
   - 连接 staging DB
   - 校验默认 tenant 存在
   - 校验至少 1 个 admin 用户存在
   - 校验 audit_log 行数与备份元数据一致（abs error < 1%）
7. 写 backup_record(type='restore_drill', status='success')
8. 输出验收清单 PDF / Markdown 给运维归档

---

## 8. 用例：权限计算（贯穿所有受保护 API）

```
[输入] user, scope, action（如 "promotion.promotion:write"）
    ↓
1. 检查 Redis perm:user:{user_id} 缓存
   - 命中 → 直接判断
    ↓
2. 缓存未命中:
   a. 加载用户的 user_role → roles
   b. 加载 role_permission → role_perms（permission_id 集合）
   c. 加载 user_permission_override → grants, revokes
   d. 计算 effective = (role_perms ∪ grants) - revokes
   e. 转换为 scope 字符串集合
   f. 写 Redis 缓存（TTL 10 分钟）
    ↓
3. 判断 scope 是否在 effective 集合中
    ↓
[输出] bool
```

---

## 9. 用例：系统初始化（首次启动）

```
应用启动 (main.py lifespan)
    ↓
1. Alembic 升级到最新版本
   - 包含 seed migration 创建：
     - default tenant
     - 10 个预设 role
     - 所有内置 permission
     - role_permission 矩阵（按代码常量同步）
    ↓
2. 启动检查:
   - SELECT EXISTS(SELECT 1 FROM user WHERE tenant_id = default_tenant AND ...has admin role...)
   - 不存在 → 创建 admin:
       password = generate_random_password(16)
       创建 user (username='admin', password_must_change=true)
       绑定 admin role
       写 audit_log("initial_admin_created")
       打印 password 到 stdout（仅一次，不存日志）
    ↓
3. 启动 FastAPI + Celery
```

---

## 10. 状态机：用户账户

```
        创建用户 → user.status=active, password_must_change=true
                                |
                     首次登录 + 改密 ↓
                                |
                              active, password_must_change=false
                              /  \
                  禁用 ↓             ↑ 启用
                  disabled         (admin 操作)
                              /  \
                 失败 10 次 ↓       ↑ admin unlock
                  locked            
                              /
                  软删除 ↓
                  deleted_at != NULL
                  （不可逆，但 audit_log 保留）
```

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| EP01-S01~S04, S07, S08 用例全部覆盖 | ✅ |
| EP10-NFR03（多租户）流程覆盖 | ✅ |
| EP10-NFR04（备份）流程覆盖 | ✅ |
| 14 个决策问题全部映射到具体流程步骤 | ✅ |
| Token 失效场景一致（密码/禁用/角色/权限/锁定/软删除） | ✅ |
| audit_log 在所有变更点都有触发 | ✅ |
| 多租户跨租户场景仅限 system_context + platform_admin | ✅ |
| 备份不依赖企微（U07 只增强告警通道） | ✅ |
