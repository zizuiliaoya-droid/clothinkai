# U03 业务规则（Business Rules）

> 单元：U03 — 博主库基础  
> 与 domain-entities.md / business-logic-model.md 配合阅读  
> 复用 U02 已建立的字段权限 / 审计脱敏 / upsert / 软删引用模式

---

## 1. 标识与唯一性

### BR-U03-01 — xiaohongshu_id 租户内唯一
- **约束**：`UNIQUE (tenant_id, xiaohongshu_id) WHERE is_deleted = false`
- **触发**：`POST /api/bloggers/`、`PUT /api/bloggers/{id}` 修改 xiaohongshu_id
- **错误**：`409 BLOGGER_XHS_ID_CONFLICT`，message=`"该博主已存在，是否查看？"`，details 含 `existing_blogger_id`
- **业务语义**：前端可根据 `existing_blogger_id` 跳转博主详情页（EP04-S01.given2）

---

## 2. 必填与格式

### BR-U03-10 — 必填字段
- `xiaohongshu_id`（≤ 64 字符）、`nickname`（≤ 128）
- 可选：phone / wechat / quote / follower_count / blogger_type / category_tags / quality_tags 等

### BR-U03-11 — 格式校验
- `xiaohongshu_id`：仅允许字母数字下划线连字符（与 style/sku code 一致）
- `phone`：MVP 阶段不强校验中国手机号格式（兼容历史导入），仅长度限制 ≤ 32
- `wechat`：长度限制 ≤ 64，无格式校验
- `follower_count`：CHECK ≥ 0（DB + service 双层）
- `quote`：DECIMAL(10,2) + CHECK ≥ 0

### BR-U03-12 — JSONB 数组校验
- `category_tags` / `quality_tags`：每项 ≤ 32 字符，数组长度 ≤ 20
- service 层 + Pydantic 双层校验

---

## 3. 删除策略

### BR-U03-20 — Blogger 软删 + 引用检查（与 BR-U02-20 一致）
- **检查**：删除 blogger 前查询 promotion 表是否引用 blogger_id
- **U03 阶段**：promotion 表不存在，`check_references()` 始终返回 0
- **TODO U04**：改为 `await self._promotion_repo.count_by_blogger(blogger_id)`
- **未引用** → 允许 `is_deleted=true`
- **已引用** → `409 BLOGGER_HAS_REFERENCE`，message=`"该博主已被推广记录引用，仅可停用"`

### BR-U03-21 — 软删可逆（恢复）
- 仅 admin 角色可调 `POST /api/bloggers/{id}/restore`
- 校验 xiaohongshu_id 是否被新博主占用
- 占用时 → `409 BLOGGER_XHS_ID_CONFLICT`，要求先重命名

### BR-U03-22 — 停用（is_active=false）
- 软停用不影响已有 promotion 引用
- 停用的博主不出现在默认搜索结果（`is_active=true`），传 `?include_inactive=true` 才包含

---

## 4. 编辑与审计

### BR-U03-30 — 编辑博主触发审计的字段（敏感字段白名单）
- **写 audit + 真实 before/after**：`xiaohongshu_id`、`nickname`
- **写 audit + 脱敏标记**：`quote_changed: true`、`wechat_changed: true`、`phone_changed: true`
- **不写 audit**：`category_tags`、`quality_tags`、`remark`、`cooperation_history`、`blogger_type`、`gender_target`、`follower_count`、`is_suspected_fake`、`is_active`、`platform`

> 与 NFR §5.3 + U02 BR-U02-31 一致：敏感值字段的真实数值不进 audit_log（仅记标记），DBA 直查 blogger 表能看到当前值，但 audit 日志不存历史值。

### BR-U03-31 — 字段未变化不写 audit（与 BR-U02-32 一致）
- service 层 dict diff 后过滤未变更字段
- 即使在敏感字段白名单内，before == after 也跳过

### BR-U03-32 — 创建博主写 audit
- action = `blogger.create`
- after 仅记 `xiaohongshu_id` + `nickname`（非敏感字段）
- 即使 quote 在创建时填了非空值，也不写 audit（与 U02 sku.create 一致）

---

## 5. 字段级权限（U02 模式延续 / U09 落细）

### BR-U03-40 — 模块权限矩阵

| 角色 | blogger:read | blogger:write | blogger:delete |
|---|---|---|---|
| 管理员 (admin) | ✅ | ✅ | ✅ |
| PR (pr) | ✅ | ✅ | ❌ |
| PR 主管 (pr_manager) | ✅ | ✅ | ✅ |
| 跟单 (merchandiser) | ✅（不见 quote/wechat/phone） | ❌ | ❌ |
| 设计师 / 设计助理 / 版师 | ✅（不见 quote/wechat/phone） | ❌ | ❌ |
| 财务 (finance) | ✅（见 quote，不见 wechat/phone） | ❌ | ❌ |
| 运营 (operations) | ✅（不见 quote/wechat/phone） | ❌ | ❌ |

### BR-U03-41 — 字段级读权限矩阵（U02 模式 / U09 前过渡）

| 角色 | quote | wechat | phone |
|---|---|---|---|
| admin | ✅ | ✅ | ✅ |
| pr | ✅ | ✅ | ✅ |
| pr_manager | ✅ | ✅ | ✅ |
| finance | ✅ | ❌ | ❌ |
| 其他 | ❌ | ❌ | ❌ |

实施常量（位于 `modules/blogger/legacy_field_permissions.py`）：
- `QUOTE_VISIBLE_ROLES = frozenset({"admin", "pr", "pr_manager", "finance"})`
- `CONTACT_VISIBLE_ROLES = frozenset({"admin", "pr", "pr_manager"})`

### BR-U03-42 — 字段级写权限

仅 **admin / pr / pr_manager** 可写 quote / wechat / phone：
- finance 仅可读 quote（不可写）
- 其他角色不可读不可写
- 违反 → `403 FIELD_PERMISSION_DENIED`（继承自 U02 `FieldPermissionDenied` 异常）

### BR-U03-43 — TODO U09 切换路径

所有硬编码位置标记 `# TODO U09: 改为 Permission.field_filter() / field_writable()`，
U09 阶段 grep `legacy_field_permissions` 一次性清理。

---

## 6. 搜索筛选规则

### BR-U03-50 — 关键字搜索（ILIKE + GIN trgm）
- 接口：`GET /api/bloggers/?keyword=xxx`
- 算法：
  ```sql
  WHERE tenant_id = :t
    AND is_deleted = false
    AND (nickname ILIKE :pattern
         OR xiaohongshu_id ILIKE :pattern
         OR wechat ILIKE :pattern)  -- 仅当用户有 CONTACT 权限时才加 wechat
  ```
- `:pattern = '%' || keyword || '%'`
- GIN trgm 索引命中：`idx_blogger_nickname_trgm` 和 `idx_blogger_xhs_id_trgm`
- wechat 字段在无权限角色搜索时**不参与匹配**（避免侧信道泄露）

### BR-U03-51 — 范围搜索（粉丝量）
- 参数：`follower_count_min` / `follower_count_max`（其一或两者）
- SQL：`WHERE follower_count >= :min AND follower_count <= :max`
- 索引：`idx_blogger_follower_count`

### BR-U03-52 — JSONB tag 包含查询
- 参数：`category_tag` / `quality_tag`（单个 tag 字符串）
- SQL：`WHERE category_tags @> :tag::jsonb`，`:tag` = `["穿搭"]`
- 索引：`idx_blogger_category_tags` / `idx_blogger_quality_tags` (GIN)

### BR-U03-53 — 复合筛选
- 多个参数 AND 关系
- 排序默认 `follower_count DESC NULLS LAST`，可选 `created_at DESC`
- 分页：page / page_size（默认 1 / 20，max 100）

### BR-U03-54 — 降级语义（与 U02 match 一致）
- 业务未匹配 → 200 + 空数组 + total=0
- 系统失败（DB 异常）→ 异常自然冒泡 → 5xx + Sentry，**不返回空结果**

---

## 7. upsert 路径（U06c 导入用，与 U02 模式一致）

### BR-U03-60 — upsert_by_xiaohongshu_id 边界（同 BR-U02-... upsert 模式）
- **数据库原子操作**：`pg_insert.on_conflict_do_update(index_elements=["tenant_id", "xiaohongshu_id"], index_where=Blogger.is_deleted.is_(False), set_=...)`
- 与 partial UNIQUE 严格对齐
- **不"恢复"软删行**：upsert 仅作用于 active 行；恢复软删走显式 endpoint
- **不暴露 HTTP**：仅 service 层内部 API；U06c 通过 `from app.modules.blogger.service import BloggerService` 直接调用
- **必须复用同一套校验/权限/审计**：
  - Pydantic schema 校验
  - 字段写权限（quote / wechat / phone）
  - audit 区分入口：`blogger.create_via_import` / `blogger.update_via_import`
  - 敏感值脱敏（`*_changed: true` 标记）

---

## 8. 错误码矩阵

| 场景 | HTTP | code |
|---|---|---|
| xiaohongshu_id 重复 | 409 | `BLOGGER_XHS_ID_CONFLICT`（含 `existing_blogger_id`） |
| 博主不存在 | 404 | `BLOGGER_NOT_FOUND` |
| follower_count < 0 | 422 | `INVALID_FOLLOWER_COUNT` |
| quote < 0 / 超精度 | 422 | `INVALID_QUOTE` |
| 字段写权限拒绝（quote/wechat/phone） | 403 | `FIELD_PERMISSION_DENIED` |
| 模块权限拒绝 | 403 | `PERMISSION_DENIED` |
| Pydantic 校验失败 | 422 | `VALIDATION_ERROR` |
| 软删时被引用 | 409 | `BLOGGER_HAS_REFERENCE`（U03 始终为 0） |
| tag 数组项超长 | 422 | `INVALID_TAG_FORMAT` |

---

## 9. 性能 / 容量预估

| 指标 | 预估 | 说明 |
|---|---|---|
| Blogger 行数 / 租户 | 1763+（业务文档基线） | 按 1.5 倍冗余 ≤ 3000 |
| GET /api/bloggers/ 列表 P95 | ≤ 200ms | 多筛选 + GIN trgm + GIN JSONB |
| GET /api/bloggers/ 关键字 P95 | ≤ 150ms | 数据量小，GIN trgm 命中即可 |
| POST 写操作 P95 | ≤ 150ms | CRUD + audit |

---

## 10. 与后续单元的契约

| 单元 | 引用方式 | 契约要求 |
|---|---|---|
| U04 推广 | `promotion.blogger_id FK` + 快照 `quote` | blogger.id 不变 |
| U06c 导入 | `BloggerService.upsert_by_xiaohongshu_id()` | xiaohongshu_id 业务键稳定 |
| U09 字段级权限 | grep `legacy_field_permissions` 替换 | 一文件清理点 |
| U10b 智能标签 | 改 `blogger_type` / `quality_tags` / `is_suspected_fake` 字段值 | 字段稳定（仅赋值方式从手填改为自动计算） |

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| 唯一约束与软删 partial 对齐 | ✅ |
| 字段权限矩阵覆盖所有角色 | ✅ |
| 审计敏感字段脱敏（quote/wechat/phone） | ✅ |
| 错误码与 U01 体系一致 + 含业务引导（existing_blogger_id） | ✅ |
| 搜索性能与表规模匹配 | ✅ |
| upsert 边界与 U02 模式一致 | ✅ |
| 与 U04/U10b 演化预留 | ✅ |
