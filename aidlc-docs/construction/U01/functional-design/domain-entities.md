# U01 领域实体（Domain Entities）

> 单元 U01 的业务实体定义。技术细节（字段类型、ORM 配置）在 Code Generation 阶段细化。

## 实体清单

| 实体 | 用途 | 阶段 |
|---|---|---|
| tenant | 租户 | MVP |
| user | 用户 | MVP |
| role | 预设角色 | MVP |
| permission | 权限定义（scope + action） | MVP |
| user_role | 用户与角色的关联（多对多） | MVP |
| user_permission_override | 用户级自定义权限授予/撤销 | MVP（U09 V1 阶段在此基础上启用字段级） |
| refresh_token | 刷新令牌追踪与吊销 | MVP |
| audit_log | 审计日志（append-only） | MVP |
| backup_record | 备份记录与保留策略 | MVP |

---

## 1. tenant（租户）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| code | VARCHAR(64) | UNIQUE NOT NULL | 租户唯一编码（业务可读） |
| name | VARCHAR(128) | NOT NULL | 租户名称 |
| status | VARCHAR(16) | NOT NULL DEFAULT 'active' | active / suspended / archived |
| max_users | INT | NULL | 用户数上限（NULL = 不限） |
| max_storage_mb | INT | NULL | 存储上限（NULL = 不限） |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | UTC |

**业务规则**：
- 租户为软删除，`deleted_at` 非空时所有相关查询返回空
- `status=suspended` 的租户禁止登录，但已登录会话保留到 token 过期
- 系统启动时若无 tenant 记录，自动创建一个 `code='default'` 的种子租户

---

## 2. user（用户）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| tenant_id | UUID | FK → tenant.id, NOT NULL | |
| username | VARCHAR(64) | UNIQUE(tenant_id, username), NOT NULL | |
| password_hash | VARCHAR(128) | NOT NULL | bcrypt 哈希 |
| display_name | VARCHAR(64) | NULL | |
| email | VARCHAR(128) | NULL | |
| status | VARCHAR(16) | NOT NULL DEFAULT 'active' | active / disabled |
| password_must_change | BOOLEAN | NOT NULL DEFAULT false | 首次登录或重置密码后置 true |
| failed_login_count | INT | NOT NULL DEFAULT 0 | 累计失败次数（账户级，用于双层策略锁账户） |
| locked_at | TIMESTAMPTZ | NULL | 账户锁定时间，非 NULL 表示已锁，需管理员解锁 |
| last_login_at | TIMESTAMPTZ | NULL | |
| password_changed_at | TIMESTAMPTZ | NOT NULL | 用于校验 token 在密码修改前签发 |
| created_at / updated_at / deleted_at | TIMESTAMPTZ | | |
| created_by / updated_by | UUID | FK → user.id, NULL | |

**唯一约束**：UNIQUE (tenant_id, username)

**业务规则**：
- `password_must_change=true` 时除"修改密码"接口外其他 API 返回 423 Locked
- `status=disabled` 或 `locked_at` 非空时所有现有 token 立即失效（通过 `password_changed_at` 比较）
- 软删除（`deleted_at`）的用户不可登录，但其历史 audit_log 保留

---

## 3. role（预设角色）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| code | VARCHAR(64) | UNIQUE NOT NULL | admin / designer / design_assistant / pattern_maker / merchandiser / pr / pr_manager / finance / operations / platform_admin |
| name | VARCHAR(64) | NOT NULL | 中文名 |
| description | TEXT | NULL | |
| is_system | BOOLEAN | NOT NULL DEFAULT true | true=预设不可删除 |

**业务规则**：
- 预设 10 个角色（9 业务角色 + 1 platform_admin 跨租户管理员），系统启动时通过 Alembic seed 写入
- `is_system=true` 的角色不可删除，但可通过 role_permission 表配置默认权限

---

## 4. permission（权限定义）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| scope | VARCHAR(128) | NOT NULL | 形如 `product.style:read` / `finance.settlement:approve` |
| name | VARCHAR(128) | NOT NULL | 中文描述 |
| category | VARCHAR(32) | NOT NULL | module / function / field |

**唯一约束**：UNIQUE (scope)

**Scope 命名规范**（Q12=B）：`<module>.<sub>:<action>`，例如：
- `auth.user:read`、`auth.user:write`
- `product.style:read`、`product.sku:write`
- `promotion.promotion:write`、`promotion.review:approve`
- `finance.settlement:approve`、`finance.settlement:pay`
- `report.publish_progress:read`

**Action 枚举**：read / write / delete / approve / export / import

**业务规则**：
- 系统启动时通过 Alembic seed 创建所有内置 scope
- 字段级权限单独建模在 `user_permission_override` 中，但 `category=field` 的 permission 用 `<entity>.<field>` 格式（Q13=A），如 `permission.scope = 'sku.cost_price'`

---

## 5. user_role（用户角色关联）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| tenant_id | UUID | FK | |
| user_id | UUID | FK | |
| role_id | UUID | FK | |
| created_at | TIMESTAMPTZ | | |

**唯一约束**：UNIQUE (tenant_id, user_id, role_id)

**业务规则**：用户可关联多个角色（兼任场景）。

---

## 6. role_permission（角色默认权限基线）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| role_id | UUID | FK | |
| permission_id | UUID | FK | |

**唯一约束**：UNIQUE (role_id, permission_id)

**业务规则**：
- Q14=A 决策：默认角色基线在 `app/modules/auth/default_roles.py` 中以代码常量声明
- 系统启动时检查并幂等同步到 DB（通过 Alembic data migration）
- 管理员**不**通过 UI 修改角色基线（避免运维灾难），如需调整通过代码 + 部署

---

## 7. user_permission_override（用户级自定义权限）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| tenant_id | UUID | FK | |
| user_id | UUID | FK | |
| permission_id | UUID | FK | |
| effect | VARCHAR(8) | NOT NULL | grant / revoke |
| reason | TEXT | NULL | 授予/撤销原因（审计） |
| created_at / created_by | | | |

**唯一约束**：UNIQUE (tenant_id, user_id, permission_id)

**业务规则**：
- 一个 (user, permission) 只能有一条 override 记录（grant 或 revoke），重复创建覆盖原值
- U01 阶段实现 module/function 级 override；U09 阶段添加 field 级 override（同一表，无需扩展）

---

## 8. refresh_token（刷新令牌）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| tenant_id | UUID | FK | |
| user_id | UUID | FK | |
| jti | VARCHAR(64) | UNIQUE NOT NULL | JWT ID |
| issued_at | TIMESTAMPTZ | NOT NULL | |
| expires_at | TIMESTAMPTZ | NOT NULL | issued_at + 7 days |
| revoked_at | TIMESTAMPTZ | NULL | 主动吊销时间 |
| user_agent | VARCHAR(256) | NULL | |
| ip | VARCHAR(64) | NULL | |

**业务规则**：
- access_token 不存库（无状态校验）；refresh_token 存库以便吊销和审计
- 密码修改 / 用户禁用 / 角色变更 / 权限矩阵变更触发该用户所有 refresh_token 批量吊销（写 revoked_at）
- 过期清理由 Celery Beat 每周触发

---

## 9. audit_log（审计日志，append-only）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGSERIAL | PK | 用 BIGSERIAL 而非 UUID 以保留时序性 |
| tenant_id | UUID | NOT NULL | system 操作可为预定义系统租户 ID |
| user_id | UUID | NULL | 系统任务为 NULL，用 actor_type 区分 |
| actor_type | VARCHAR(16) | NOT NULL | user / system / worker / platform_admin |
| action | VARCHAR(64) | NOT NULL | login / login_failed / password_change / decrypt / role_change ... |
| resource | VARCHAR(64) | NULL | user / credential / settlement ... |
| resource_id | VARCHAR(64) | NULL | |
| before | JSONB | NULL | 变更前快照（仅敏感字段） |
| after | JSONB | NULL | 变更后快照（仅敏感字段） |
| purpose | VARCHAR(128) | NULL | 解密时的用途说明 |
| ip | VARCHAR(64) | NULL | |
| user_agent | VARCHAR(256) | NULL | |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT NOW() | |

**约束（数据库层）**：
- 无 UPDATE 权限：`REVOKE UPDATE ON audit_log FROM <app_role>`
- 无 DELETE 权限：`REVOKE DELETE ON audit_log FROM <app_role>`
- 仅允许 INSERT 和 SELECT

**索引**：
- `(tenant_id, created_at DESC)` 用于按租户时间倒序查询
- `(tenant_id, action, created_at DESC)` 按操作类型筛选
- `(tenant_id, user_id, created_at DESC)` 按操作人查询

**保留策略**（Q8=B）：
- DB 保留最近 1 年
- 超过 1 年的记录由 Celery 任务每月归档到 R2 `backups/audit-archive/{year}-{month}.jsonl.gz`
- 归档完成后从 DB 删除（特殊豁免：通过专门的 archiver_role 在事务中执行 DELETE，不破坏 append-only 模式对应用层的语义）

---

## 10. backup_record（备份记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | |
| backup_type | VARCHAR(16) | NOT NULL | daily / monthly / manual / restore_drill |
| started_at | TIMESTAMPTZ | NOT NULL | |
| completed_at | TIMESTAMPTZ | NULL | |
| status | VARCHAR(16) | NOT NULL | running / success / failed |
| includes | JSONB | NOT NULL | 包含的内容清单：`["pg_dump", "credentials_bucket", "config_export"]` |
| r2_key | VARCHAR(256) | NULL | R2 中的对象键 |
| size_bytes | BIGINT | NULL | |
| checksum | VARCHAR(64) | NULL | SHA256 |
| error_message | TEXT | NULL | |
| retention_until | DATE | NULL | 保留截止日（按 30 天每日 + 每月 1 份保留 1 年策略） |

**业务规则**：
- daily 备份保留 30 天
- 每月 1 日的 daily 备份升级为 monthly，retention_until 设为 1 年后
- 超过 retention_until 的备份由清理任务自动删除 R2 对象 + 删除 backup_record（这条记录的 DELETE 是允许的，与 audit_log 不同）

---

## 11. ER 简图

```
tenant (1)─────────(n) user
                       │
                       │ (n)
                       ↓
                   user_role (n)─────(1) role (n)──(role_permission)──(n) permission
                       │
                       │ (n)
                       ↓
              user_permission_override ─→ permission

tenant (1)─────────(n) refresh_token (← user)
tenant (1)─────────(n) audit_log     (← user, optional)
  独立 (无 tenant_id)─────── backup_record  (备份是全局任务)
```

> backup_record 不带 tenant_id（备份是平台级任务）；其余所有业务表（tenant 自身除外）必须带 tenant_id。

---

## 12. 一致性校验

| 校验 | 结果 |
|---|---|
| 实体清单覆盖 EP01-S01~S04, S07, S08 | ✅ |
| 实体覆盖 EP10-NFR03（多租户）+ EP10-NFR04（备份） | ✅（tenant + backup_record） |
| 与 components.md 第 4.1 节 ORM 模型清单一致 | ✅ |
| 唯一约束都带 tenant_id（除 platform_admin 跨租户实体） | ✅ |
| audit_log append-only 在数据库层强制 | ✅（REVOKE UPDATE/DELETE） |
| 字段命名 snake_case | ✅ |
| 所有业务表含 created_at / updated_at / deleted_at | ✅（除 audit_log 永不软删） |
