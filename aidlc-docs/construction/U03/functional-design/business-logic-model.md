# U03 业务逻辑模型（Business Logic Model）

> 单元：U03 — 博主库基础  
> 形式：每个核心 Use Case 一段 ASCII 流程 + 步骤说明 + 错误分支  
> 关联：domain-entities.md / business-rules.md

---

## 1. Use Case 总览

| UC | 名称 | 关联故事 | API |
|---|---|---|---|
| UC-1 | 创建博主 | EP04-S01 | `POST /api/bloggers/` |
| UC-2 | 编辑博主 | EP04-S02 | `PUT /api/bloggers/{id}` |
| UC-3 | 搜索博主 | EP04-S03 | `GET /api/bloggers/` |
| UC-4 | 详情查询 | — | `GET /api/bloggers/{id}` |
| UC-5 | 软删 / 停用 / 恢复 | — | `DELETE / disable / restore` |
| UC-6 | upsert（U06c 用，不暴露 HTTP） | — | `BloggerService.upsert_by_xiaohongshu_id` |

---

## 2. UC-1 创建博主（POST /api/bloggers/）

### 2.1 流程

```
POST /api/bloggers/  body: BloggerCreate
      │
      ▼
[1] @require_permission("blogger:write")
      │  ├──[Q]── 401 / 403 → 终止
      ▼
[2] Pydantic BloggerCreate 校验
      │  - xiaohongshu_id 长度 + 字符集
      │  - tags 数组项校验（≤ 32 字符 / ≤ 20 项）
      │  - quote / follower_count CHECK ≥ 0
      │  ├──[Q]── 422 VALIDATION_ERROR → 终止
      ▼
[3] BloggerService.create_blogger(payload, user)
      │
      ├─ 3.1 BR-U03-01: 校验 xiaohongshu_id 唯一
      │      SELECT id FROM blogger
      │       WHERE tenant_id=:t
      │         AND xiaohongshu_id=:xhs
      │         AND is_deleted=false
      │      
      │      ├──[Q]── 409 BLOGGER_XHS_ID_CONFLICT
      │      │       details = {"existing_blogger_id": ...}
      │      │       message = "该博主已存在，是否查看？"
      │
      ├─ 3.2 BR-U03-42: 字段写权限（quote / wechat / phone）
      │      仅 admin / pr / pr_manager 可写敏感字段
      │      ├──[Q]── 403 FIELD_PERMISSION_DENIED
      │
      ├─ 3.3 创建 Blogger 实体
      │      blogger.tenant_id = ctx.tenant_id (自动)
      │      blogger.is_active = true
      │      blogger.is_deleted = false
      │      blogger.is_suspected_fake = false  (默认)
      │
      ├─ 3.4 db.add(blogger) + db.flush()
      │      ORM 钩子自动写 created_at / updated_at
      │      RLS 策略检查 tenant_id 匹配（U01 实现）
      │
      ├─ 3.5 BR-U03-32: 写 audit
      │      action = 'blogger.create'
      │      after = {"xiaohongshu_id": ..., "nickname": ...}
      │      不记 quote / wechat / phone（敏感值不入 audit）
      │
      ├─ 3.6 db.commit()
      │
      ▼
[4] 返回 BloggerResponse（service._to_response 按角色过滤敏感字段）
      │
      ▼
[前端] 201 Created
```

### 2.2 错误矩阵

| 步骤 | 触发 | HTTP | code |
|---|---|---|---|
| 1 | 无 token / token 无效 | 401 | `TOKEN_INVALID` |
| 1 | 无 blogger:write 权限 | 403 | `PERMISSION_DENIED` |
| 2 | Pydantic 校验失败 | 422 | `VALIDATION_ERROR` |
| 3.1 | xiaohongshu_id 重复 | 409 | `BLOGGER_XHS_ID_CONFLICT` |
| 3.2 | 角色无权写 quote/wechat/phone | 403 | `FIELD_PERMISSION_DENIED` |

### 2.3 验收映射

- **EP04-S01.given1**：PR 已登录 → 创建成功 + UNIQUE 生效 ✅
- **EP04-S01.given2**：重复 xiaohongshu_id → 409 + `existing_blogger_id` 引导 ✅

---

## 3. UC-2 编辑博主（PUT /api/bloggers/{id}）

### 3.1 流程

```
PUT /api/bloggers/{id}  body: BloggerUpdate（部分字段 PATCH 语义）
      │
      ▼
[1] @require_permission("blogger:write")
      │
      ▼
[2] Pydantic BloggerUpdate 校验
      │
      ▼
[3] BloggerService.update_blogger(id, payload, user)
      │
      ├─ 3.1 取 existing blogger（RLS 自动跨租户过滤为 None）
      │      └──[Q]── 404 BLOGGER_NOT_FOUND
      │
      ├─ 3.2 BR-U03-01: 改 xiaohongshu_id 时唯一性校验
      │      └──[Q]── 409 BLOGGER_XHS_ID_CONFLICT
      │
      ├─ 3.3 BR-U03-42: 字段写权限校验
      │      payload 含 quote/wechat/phone 任一 + user 无权 → 403
      │
      ├─ 3.4 dict diff: 计算实际变更字段
      │      changes = {field: {"before": ..., "after": ...}}
      │
      ├─ 3.5 BR-U03-31: 字段未变化 → 直接返回 existing
      │
      ├─ 3.6 应用变更（设置 ORM 字段）
      │
      ├─ 3.7 BR-U03-30: audit_safe_changes 转换
      │      白名单：xiaohongshu_id / nickname / quote / wechat / phone
      │      其中 quote / wechat / phone 仅记 *_changed: true
      │      其他字段（tags / remark / blogger_type 等）不写 audit
      │
      ├─ 3.8 写 audit + commit
      │
      ▼
[4] 返回 BloggerResponse（按角色过滤）
```

### 3.2 关键示例

修改 quote 时 audit_log 内容：
```json
{
  "action": "blogger.update",
  "resource": "blogger",
  "resource_id": "...",
  "after": {"quote_changed": true}
}
```

修改 nickname 时：
```json
{
  "action": "blogger.update",
  "before": {"nickname": "旧昵称"},
  "after": {"nickname": "新昵称"}
}
```

### 3.3 验收映射

- **EP04-S02**：编辑成功 + audit_log 记录字段变更（quote 必记，但脱敏）✅

---

## 4. UC-3 搜索博主（GET /api/bloggers/）

### 4.1 流程

```
GET /api/bloggers/?keyword=xxx&blogger_type=KOL&follower_count_min=10000
                  &category_tag=穿搭&quality_tag=高互动
                  &page=1&page_size=20
      │
      ▼
[1] @require_permission("blogger:read")
      │
      ▼
[2] BloggerService.list(filters, page, page_size, user)
      │
      ├─ 2.1 build_query()
      │      base = SELECT * FROM blogger WHERE tenant_id=:t AND is_deleted=false
      │      
      │      if not include_inactive:
      │         base.where(is_active=true)
      │
      │      # BR-U03-50: 关键字搜索（命中 GIN trgm）
      │      if keyword:
      │         clauses = [nickname ILIKE :p, xiaohongshu_id ILIKE :p]
      │         if user has CONTACT_VISIBLE_ROLES:
      │            clauses.append(wechat ILIKE :p)
      │         base.where(or_(*clauses))
      │
      │      # BR-U03-51: 范围筛选（命中 idx_blogger_follower_count）
      │      if follower_count_min:
      │         base.where(follower_count >= :min)
      │      if follower_count_max:
      │         base.where(follower_count <= :max)
      │
      │      # BR-U03-52: JSONB tag 包含查询（命中 GIN JSONB）
      │      if category_tag:
      │         base.where(category_tags @> :tag::jsonb)
      │      if quality_tag:
      │         base.where(quality_tags @> :tag::jsonb)
      │
      │      # 普通筛选
      │      if blogger_type:
      │         base.where(blogger_type == :type)
      │      if platform:
      │         base.where(platform == :platform)
      │      if is_suspected_fake is not None:
      │         base.where(is_suspected_fake == :flag)
      │
      ├─ 2.2 总数：SELECT COUNT(*) FROM (...) sub
      │
      ├─ 2.3 数据：base ORDER BY follower_count DESC NULLS LAST
      │              LIMIT :page_size OFFSET (page-1)*page_size
      │
      ├─ 2.4 应用 BR-U03-41: _to_response 按角色过滤
      │      quote / wechat / phone 不可见角色 → None
      │
      ▼
[3] 返回 BloggerPage<items, total, page, page_size>
```

### 4.2 关键性能要点

- 1763+ 博主 / 租户场景：GIN trgm + GIN JSONB 索引足够
- 数据量小，所有筛选条件组合 P95 ≤ 200ms
- 拼接表达式不需要（与 U02 区别：表数量级小）

### 4.3 降级语义（同 U02 match）

- 业务未匹配（合法查询零结果）→ 200 + 空数组 + total=0
- 系统失败 → 异常自然冒泡 → 5xx + Sentry

### 4.4 验收映射

- **EP04-S03.given1**：多条件组合查询返回符合条件博主 + 分页排序 ✅
- **EP04-S03.given2**：PR 角色看到的 quote 字段按权限规则可见或屏蔽 ✅

---

## 5. UC-4 详情查询（GET /api/bloggers/{id}）

### 5.1 流程

```
GET /api/bloggers/{id}
      │
      ▼
[1] @require_permission("blogger:read")
      │
      ▼
[2] BloggerService.get_blogger(id, user)
      │
      ├─ 2.1 取 blogger（RLS 自动）
      │      └──[Q]── 404
      │
      ├─ 2.2 应用字段过滤（同 list）
      │
      ▼
[3] 返回 BloggerResponse
```

---

## 6. UC-5 软删 / 停用 / 恢复

### 6.1 软删 `DELETE /api/bloggers/{id}`
```
[1] @require_permission("blogger:delete")  # 仅 admin / pr_manager
[2] 取 blogger ──→ 404 if not found
[3] BR-U03-20: check_references()
    U03 阶段：promotion 表不存在，refs = 0
    TODO U04: 查 promotion 表
    
    if total_refs > 0:
       409 BLOGGER_HAS_REFERENCE
[4] blogger.is_deleted = true
[5] @audit("blogger.delete")
[6] commit + 返回 204
```

### 6.2 停用 `POST /api/bloggers/{id}/disable`
```
[1] @require_permission("blogger:write")
[2] blogger.is_active = false
[3] @audit("blogger.disable")
[4] 返回 BloggerResponse
```

### 6.3 恢复 `POST /api/bloggers/{id}/restore`
```
[1] @require_permission("blogger:delete")  # admin
[2] BR-U03-21: 校验 xiaohongshu_id 是否被新博主占用
    if exists: 409 BLOGGER_XHS_ID_CONFLICT
[3] blogger.is_deleted = false; is_active = true
[4] @audit("blogger.restore")
[5] 返回 BloggerResponse
```

---

## 7. UC-6 upsert（U06c 内部 API）

### 7.1 流程

```
[U06c 适配器] await BloggerService.upsert_by_xiaohongshu_id(payload, user)
      │
      ▼
[1] 业务规则校验（与 create 完全相同）
      ├─ Pydantic（schema 已校验）
      ├─ BR-U03-42: 字段写权限
      │
      ▼
[2] 数据库原子 upsert（与 U02 SkuRepository.upsert_atomic 同模式）
      stmt = pg_insert(Blogger).values(...)
      stmt = stmt.on_conflict_do_update(
          index_elements=[tenant_id, xiaohongshu_id],
          index_where=Blogger.is_deleted.is_(False),
          set_={
              "nickname": stmt.excluded.nickname,
              "platform": stmt.excluded.platform,
              "wechat": stmt.excluded.wechat,
              "phone": stmt.excluded.phone,
              "follower_count": stmt.excluded.follower_count,
              "blogger_type": stmt.excluded.blogger_type,
              "gender_target": stmt.excluded.gender_target,
              "category_tags": stmt.excluded.category_tags,
              "quality_tags": stmt.excluded.quality_tags,
              "quote": stmt.excluded.quote,
              "cooperation_history": stmt.excluded.cooperation_history,
              "remark": stmt.excluded.remark,
              "is_suspected_fake": stmt.excluded.is_suspected_fake,
              "is_active": stmt.excluded.is_active,
              "updated_at": sa.func.now(),
              # 不更新：id / tenant_id / created_at / xiaohongshu_id / is_deleted
          },
      ).returning(Blogger, sa.text("(xmax = 0) AS is_inserted"))
      │
      ▼
[3] 审计区分入口
      action = 'blogger.create_via_import' if is_inserted
               else 'blogger.update_via_import'
      after = 脱敏后的字段标记
      │
      ▼
[4] commit + 返回 BloggerResponse
```

### 7.2 边界

| 约束 | 说明 |
|---|---|
| 不暴露 HTTP | service 层内部 API；U06c 通过 `from app.modules.blogger.service import BloggerService` 调用 |
| 复用同一套校验 | Pydantic / 字段权限 / 审计 / 唯一约束 |
| 与 partial UNIQUE 对齐 | `index_where=Blogger.is_deleted.is_(False)` |
| 不"恢复"软删行 | 恢复软删走 BR-U03-21 显式接口 |
| 审计区分入口 | 4 个 action：blogger.create / update / create_via_import / update_via_import |

---

## 8. 端到端时序

```
PR 添加博主 → 编辑报价 → PR 主管搜索筛选 → 后续 U04 推广合作引用
```

```
PR ──创建博主──> BloggerService.create_blogger ──> DB ──> audit_log
                                                              │
PR ──编辑 quote──> BloggerService.update_blogger ──> DB ──> audit_log {quote_changed: true}
                                                              │
PR 主管 ──搜索 KOL+穿搭──> BloggerService.list ──> 返回过滤后 BloggerPage
                                                              │
（U04 阶段）promotion 引用 blogger_id + 快照 quote ──> [U04 范围]
```

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 6 个 UC 覆盖 EP04-S01~S03 全部验收 | ✅ |
| 错误流程明确，每个 422/409/403 都有触发条件 | ✅ |
| 审计敏感字段脱敏（quote/wechat/phone 仅记 *_changed） | ✅ |
| 字段级权限模式与 U02 一致 | ✅ |
| 软删/恢复/停用三种状态语义清晰 | ✅ |
| upsert 流程严格复用 U02 模式 | ✅ |
| 引用检查为 U04 启用预留接口 | ✅ |
| 搜索流程含降级语义（业务 vs 系统失败） | ✅ |
