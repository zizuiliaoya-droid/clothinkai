# U04 业务逻辑模型（Business Logic Model）

> 单元：U04 — 推广合作核心  
> 形式：每个核心 Use Case 一段 ASCII 流程 + 步骤说明 + 错误分支  
> 关联：domain-entities.md / business-rules.md

---

## 1. Use Case 总览

| UC | 名称 | 关联故事 | API |
|---|---|---|---|
| UC-1 | 创建推广（含快照 + 重复检测 + 序列号生成） | EP05-S02/S03/S04 | `POST /api/promotions/` |
| UC-2 | 编辑推广（含字段权限） | EP05-S02 | `PUT /api/promotions/{id}` |
| UC-3 | 列表查询（含 urge_status / dual_platform / cpl 计算） | EP05-S01/S05/S06 | `GET /api/promotions/` |
| UC-4 | 详情查询 | — | `GET /api/promotions/{id}` |
| UC-5 | 标记已发布（PR） | EP05-S07 | `PUT /api/promotions/{id}/publish` |
| UC-6 | 取消合作（PR） | EP05-S08 | `PUT /api/promotions/{id}/cancel` |
| UC-7 | 召回流程（PR） | EP05-S09 | `PUT /api/promotions/{id}/recall/*` |
| UC-8 | 审核（PR 主管） | EP05-S13 | `POST /api/promotions/{id}/review` |
| UC-9 | 更新点赞量（U13 内部 API） | EP05-S10/S11/S12 | `PromotionService.update_like_count()` |

---

## 2. UC-1 创建推广（POST /api/promotions/）

### 2.1 流程

```
POST /api/promotions/  body: PromotionCreate
      │
      ▼
[1] @require_permission("promotion:write")
      │  ├──[Q]── 401 / 403 → 终止
      ▼
[2] Pydantic PromotionCreate 校验
      │  - style_id / blogger_id 必填 + UUID 格式
      │  - cooperation_date 必填
      │  - platform Platform 枚举
      │  ├──[Q]── 422 VALIDATION_ERROR
      ▼
[3] PromotionService.create_promotion(payload, user)
      │
      ├─ 3.1 BR-U04-11: 校验 style_id / blogger_id 引用
      │      style = await style_repo.get_by_id(payload.style_id)
      │      if style is None or style.is_deleted:
      │         raise InvalidStyleReferenceError()
      │      blogger = await blogger_repo.get_by_id(payload.blogger_id)
      │      if blogger is None or blogger.is_deleted:
      │         raise InvalidBloggerReferenceError()
      │      
      │      if payload.sku_id:
      │         sku = await sku_repo.get_by_id(payload.sku_id)
      │         if sku is None or sku.style_id != style.id:
      │            raise InvalidSkuReferenceError()
      │
      ├─ 3.2 BR-U04-12: 快照字段填充
      │      style_code_snapshot = style.style_code
      │      style_short_name_snapshot = style.short_name OR style.style_name
      │      
      │      # quote_amount: payload 优先，否则 blogger.quote 快照
      │      if payload.quote_amount is not None:
      │         quote_amount = payload.quote_amount
      │      elif blogger.quote is not None:
      │         quote_amount = blogger.quote
      │      else:
      │         raise ValidationError("quote_amount 必填或 blogger.quote 不能为空")
      │      
      │      # cost_snapshot: 仅当 sku_id 提供
      │      cost_snapshot = sku.cost_price if sku_id else None
      │
      ├─ 3.3 字段写权限校验（quote_amount/cost_snapshot 的 hardcode）
      │      if payload contains 这些字段 + user 无权 → 403
      │
      ├─ 3.4 BR-U04-01/02: 生成 internal_code（行级锁 + 同事务）
      │      with session.begin_nested():  # SAVEPOINT
      │         seq = SELECT * FROM promotion_sequence
      │                WHERE tenant_id=:t AND date_key=:cooperation_date
      │                FOR UPDATE
      │         if seq is None:
      │            INSERT INTO promotion_sequence (tenant_id, date_key, last_seq) VALUES (..., 1)
      │            new_seq = 1
      │         else:
      │            UPDATE promotion_sequence SET last_seq = last_seq + 1
      │            new_seq = seq.last_seq + 1
      │         
      │         if new_seq > 9999:
      │            raise SequenceOverflowError()
      │         
      │         tenant_prefix = (tenant.code + "X")[:2].upper()
      │         internal_code = f"{tenant_prefix}{cooperation_date.strftime('%y%m%d')}{new_seq:04d}"
      │
      ├─ 3.5 BR-U04-40: 同款博主重复检测（不阻塞）
      │      duplicates = SELECT id, internal_code FROM promotion
      │                    WHERE tenant_id=:t AND style_id=:s AND blogger_id=:b
      │                      AND publish_status IN ('未发布', '已发布')
      │                      AND is_active = true
      │      warnings = [
      │         {code: 'DUPLICATE_PROMOTION', existing_promotion_id: ..., existing_internal_code: ...}
      │      ] if duplicates else []
      │
      ├─ 3.6 创建 Promotion 实体
      │      promotion = Promotion(
      │         tenant_id=ctx.tenant_id,  # 自动
      │         style_id=..., sku_id=..., blogger_id=..., pr_id=user.id,
      │         internal_code=internal_code,
      │         style_code_snapshot=..., style_short_name_snapshot=...,
      │         quote_amount=..., cost_snapshot=...,
      │         platform=..., cooperation_date=...,
      │         publish_status='未发布',
      │         recall_status='未召回',
      │         settlement_status='未核查',
      │      )
      │      session.add(promotion)
      │      await session.flush()
      │
      ├─ 3.7 BR-U04-61: 写 audit
      │      action = 'promotion.create'
      │      after = {internal_code, style_id, blogger_id, platform, cooperation_date}
      │      （quote_amount 脱敏）
      │
      ├─ 3.8 commit（promotion + sequence 同事务）
      │
      ▼
[4] 返回 PromotionCreateResponse{
       data: PromotionResponse(...),  # 字段过滤 + 计算字段填充
       warnings: [...] | []
    }
```

### 2.2 错误矩阵

| 步骤 | 触发 | HTTP | code |
|---|---|---|---|
| 1 | 无 token | 401 | `TOKEN_INVALID` |
| 1 | 无 promotion:write | 403 | `PERMISSION_DENIED` |
| 2 | Pydantic 失败 | 422 | `VALIDATION_ERROR` |
| 3.1 | style_id 不存在 | 422 | `INVALID_STYLE_REFERENCE` |
| 3.1 | blogger_id 不存在 | 422 | `INVALID_BLOGGER_REFERENCE` |
| 3.1 | sku_id 不存在 / 跨 style | 422 | `INVALID_SKU_REFERENCE` |
| 3.2 | quote_amount 缺失且 blogger.quote 为空 | 422 | `VALIDATION_ERROR` |
| 3.3 | 角色无权写 quote_amount/cost_snapshot | 403 | `FIELD_PERMISSION_DENIED` |
| 3.4 | sequence > 9999 | 409 | `SEQUENCE_OVERFLOW` |

### 2.3 验收映射

- **EP05-S02**：创建 + internal_code 自动生成 + UNIQUE ✅
- **EP05-S02**：style_id / blogger_id 不存在返回 422 ✅
- **EP05-S03**：自动按款号填充商品简称（通过快照 style_short_name_snapshot）✅
- **EP05-S04**：重复检测返回 warning 不阻塞 ✅

---

## 3. UC-5 标记已发布（PUT /api/promotions/{id}/publish）

### 3.1 流程

```
PUT /api/promotions/{id}/publish  body: PromotionPublish
      │
      ▼
[1] @require_permission("promotion:write")
      │
      ▼
[2] Pydantic PromotionPublish 校验
      │  - publish_url 必填 + URL 格式
      │  - actual_publish_date 必填
      ▼
[3] PromotionService.publish(promotion_id, payload, user)
      │
      ├─ 3.1 取 promotion ──→ 404 if not found
      │
      ├─ 3.2 状态机校验
      │      transition: '未发布' --publish--> '已发布'
      │      若 publish_status != '未发布':
      │         raise IllegalStateTransitionError(from_state, '已发布', 'publish')
      │
      ├─ 3.3 应用变更
      │      promotion.publish_status = '已发布'
      │      promotion.publish_url = payload.publish_url
      │      promotion.actual_publish_date = payload.actual_publish_date
      │
      ├─ 3.4 自动推进 settlement_status（同事务 BR-U04-22）
      │      if promotion.settlement_status == '未核查':
      │         promotion.settlement_status = '待核查'
      │         await audit.log(action='promotion.settlement_status.auto_advance', ...)
      │
      ├─ 3.5 await session.flush()
      │
      ├─ 3.6 audit log: action='promotion.publish'
      │      after = {publish_url, actual_publish_date, publish_status='已发布'}
      │
      ├─ 3.7 发出 PromotionPublished 事件（同事务）
      │      event = PromotionPublished(
      │         event_id=uuid4(),
      │         tenant_id=..., promotion_id=..., publish_url=..., publish_date=...
      │      )
      │      await events.dispatch(event)  # U07 监听（U04 阶段无 listener）
      │
      ├─ 3.8 commit
      │
      ▼
[4] 返回 PromotionResponse
```

### 3.2 验收映射
- **EP05-S07.given1**：publish_status="未发布" → publish_url 填入 → "已发布" ✅
- **EP05-S07.given2**：publish_url 非法 URL → 422（Pydantic 层）✅

---

## 4. UC-6 取消合作（PUT /api/promotions/{id}/cancel）

```
PUT /api/promotions/{id}/cancel  body: PromotionCancel
      │
      ▼
[1] @require_permission("promotion:write")
      │
      ▼
[2] Pydantic 校验 cancel_reason 必填
      │
      ▼
[3] PromotionService.cancel(promotion_id, payload, user)
      │
      ├─ 3.1 取 promotion ──→ 404
      │
      ├─ 3.2 状态机校验
      │      if promotion.publish_status == '已发布':
      │         raise IllegalStateTransitionError() with code='CANCEL_NOT_ALLOWED_FOR_PUBLISHED'
      │      if promotion.publish_status not in {'未发布', '异常'}:
      │         raise IllegalStateTransitionError()
      │
      ├─ 3.3 应用变更
      │      promotion.publish_status = '已取消'
      │      promotion.cancel_reason = payload.cancel_reason
      │
      ├─ 3.4 audit: action='promotion.cancel'
      │      after = {publish_status='已取消', cancel_reason=...}
      │
      ├─ 3.5 commit
      │
      ▼
[4] 返回 PromotionResponse
```

### 验收映射
- **EP05-S08.given1**：未发布 → 取消成功 ✅
- **EP05-S08.given2**：已发布 → 422 + 提示走召回 ✅

---

## 5. UC-7 召回流程（3 个子端点）

### 5.1 启动召回 `PUT /api/promotions/{id}/recall/start`

```
[1] @require_permission("promotion:write")
[2] PromotionService.start_recall(id, payload, user)
    ├─ 取 promotion ──→ 404
    ├─ 跨状态机校验：publish_status ∈ {已发布, 已取消}（否则 422 RECALL_NOT_ALLOWED）
    ├─ 状态机：未召回/召回失败 --start_recall--> 召回中
    ├─ promotion.recall_status = '召回中'
    ├─ promotion.recall_reason = payload.recall_reason  # 可选
    ├─ audit: action='promotion.recall_start'
    └─ commit
[3] 返回 PromotionResponse
```

### 5.2 召回成功 `PUT /api/promotions/{id}/recall/success`

```
[1] @require_permission("promotion:write")
[2] PromotionService.recall_success(id, user)
    ├─ 状态机：召回中 --recall_success--> 召回成功（终态不可逆）
    ├─ audit: action='promotion.recall_success'
    └─ commit
```

### 5.3 召回失败 `PUT /api/promotions/{id}/recall/failure`

```
状态机：召回中 --recall_failure--> 召回失败（可重新发起 start_recall）
audit: action='promotion.recall_failure'
```

### 验收映射
- **EP05-S09.given1**：已取消 → start_recall → 召回中 ✅
- **EP05-S09.given2**：召回中 → success / failure ✅
- **EP05-S09**：召回成功不可逆 / 失败可重新发起 ✅

---

## 6. UC-8 审核（POST /api/promotions/{id}/review）

### 6.1 流程

```
POST /api/promotions/{id}/review  body: PromotionReview
      │
      ▼
[1] @require_permission("promotion:review")  # pr_manager + admin
      │
      ▼
[2] Pydantic PromotionReview
      │  - action: ReviewAction 枚举（approve / reject）
      │  - review_reason: action='reject' 时必填
      ▼
[3] PromotionService.review(promotion_id, payload, user)
      │
      ├─ 3.1 取 promotion ──→ 404
      │
      ├─ 3.2 BR-U04-64: 自审禁止
      │      if promotion.pr_id == user.id:
      │         raise SelfReviewNotAllowedError()
      │
      ├─ 3.3 跨状态机校验
      │      if promotion.publish_status != '已发布':
      │         raise IllegalStateTransitionError()
      │      if promotion.settlement_status != '待核查':
      │         raise IllegalStateTransitionError()
      │
      ├─ 3.4 应用变更
      │      promotion.reviewed_by = user.id
      │      promotion.reviewed_at = now()
      │      promotion.review_action = payload.action.value
      │      promotion.review_reason = payload.review_reason
      │      
      │      if payload.action == APPROVE:
      │         promotion.settlement_status = '待付款'
      │      else:  # REJECT
      │         promotion.settlement_status = '已驳回'
      │
      ├─ 3.5 await session.flush()
      │
      ├─ 3.6 BR-U04-62: audit
      │      action = 'promotion.review_approve' or 'promotion.review_reject'
      │      before = {settlement_status='待核查'}
      │      after = {settlement_status, review_action, review_reason}
      │      （quote_amount 脱敏不出现）
      │
      ├─ 3.7 仅 approve 路径：发 SettlementRequested 事件
      │      if payload.action == APPROVE:
      │         event = SettlementRequested(
      │            event_id=uuid4(),
      │            timestamp=now(),
      │            tenant_id=user.tenant_id,
      │            promotion_id=promotion.id,
      │            promotion_internal_code=promotion.internal_code,
      │            blogger_id=promotion.blogger_id,
      │            style_id=promotion.style_id,
      │            amount=promotion.quote_amount,
      │            requested_by=user.id,
      │            requested_at=promotion.reviewed_at,
      │         )
      │         await events.dispatch(event)
      │         # U05 监听器在同事务内创建 settlement 记录（DB UNIQUE(promotion_id) 兜底）
      │         # U05 端异常 → 整个事务回滚（U04 review 不成功）
      │
      ├─ 3.8 commit（promotion + (U05) settlement 同事务）
      │
      ▼
[4] 返回 PromotionResponse
```

### 6.2 验收映射

- **EP05-S13.given1**：approve → settlement_status="待付款" + 发 SettlementRequested 事件 ✅
- **EP05-S13.given2**：reject + reason → settlement_status="已驳回" ✅
- **关键决策**：U04 不直接创建 settlement，仅发事件，由 U05 创建（与 INCEPTION 决策一致）

---

## 7. UC-3 列表查询（GET /api/promotions/）

### 7.1 流程

```
GET /api/promotions/?keyword=...&pr_id=...&publish_status=已发布
                      &urge_status=催发&cooperation_date_from=...&cooperation_date_to=...
                      &is_hit=true&dual_platform=true
                      &page=1&page_size=20
      │
      ▼
[1] @require_permission("promotion:read")
      │
      ▼
[2] PromotionService.list_promotions(filters, page, page_size, user)
      │
      ├─ 2.1 build_query() — 使用 CTE 注入计算列
      │      
      │      WITH base AS (
      │        SELECT p.*,
      │               -- urge_status 计算（CASE 表达式）
      │               CASE ... END AS urge_status,
      │               -- dual_platform 计算（EXISTS 子查询）
      │               EXISTS (...) AS dual_platform
      │        FROM promotion p
      │        WHERE p.tenant_id = :t
      │          AND p.is_active = true
      │      )
      │      SELECT * FROM base
      │      WHERE 1=1
      │        AND keyword 筛选（ILIKE 多字段 OR 命中 GIN trgm）
      │        AND pr_id / blogger_id / style_id / platform / publish_status 等
      │        AND urge_status = :urge_status_filter
      │        AND cooperation_date BETWEEN :from AND :to
      │        AND like_count >= :hit_threshold IF is_hit=true
      │      ORDER BY cooperation_date DESC, created_at DESC
      │      LIMIT :ps OFFSET (:p-1)*:ps
      │
      ├─ 2.2 总数查询（同上但 SELECT COUNT(*)）
      │
      ├─ 2.3 service 层后处理：
      │      for each promotion in items:
      │         # 计算 effective_like_count / is_hit / cpl
      │         eff_likes = calculate_effective_like_count(p.platform, p.like_count)
      │         is_hit = calculate_is_hit(p.like_count)
      │         cpl = calculate_cpl(p.quote_amount, eff_likes)
      │         
      │         # 字段权限过滤（hardcode）
      │         response = _to_response(p, user, eff_likes, is_hit, cpl, p.urge_status, p.dual_platform)
      │
      ▼
[3] 返回 PromotionPage
```

### 7.2 验收映射
- **EP05-S05** dual_platform 自动标记 ✅
- **EP05-S06** urge_status 实时计算 ✅
- **EP05-S10** effective_like_count 平台折算 ✅
- **EP05-S11** is_hit 阈值实时计算 ✅
- **EP05-S12** cpl 实时计算 + 零分母 null ✅

---

## 8. UC-9 update_like_count（U13 内部 API）

### 8.1 流程

```python
# modules/promotion/service.py
class PromotionService:
    async def update_like_count(
        self,
        promotion_id: UUID,
        like_count: int,
        source: Literal["user", "crawler"],
        user_id: UUID | None = None,
    ) -> Promotion:
        """U13 数据采集 Worker 调用此内部 API 更新点赞量。
        
        不暴露 HTTP（U13 通过 from app.modules.promotion.service import 调用）。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError()
        
        if like_count < 0:
            raise ValidationError("like_count 不能为负数")
        
        old_like_count = promotion.like_count
        if old_like_count == like_count:
            return promotion  # 无变更
        
        promotion.like_count = like_count
        await self._session.flush()
        
        # BR-U04-63: 区分采集 vs 用户编辑
        action = (
            "promotion.like_count_updated_by_crawler"
            if source == "crawler"
            else "promotion.like_count_updated_by_user"
        )
        await self._audit.log(
            action=action,
            resource="promotion",
            resource_id=promotion.id,
            before={"like_count": old_like_count},
            after={"like_count": like_count},
            user_id=user_id,
        )
        await self._session.commit()
        return promotion
```

### 8.2 关键设计
- 不暴露 HTTP 端点（U13 通过 service 内部 API 调用）
- audit action 区分 crawler vs user 来源
- like_count 在 audit 白名单内（采集频次高也要记录）

---

## 9. 端到端时序

```
PR 创建推广 → 自动 internal_code → 重复检测警告
   ↓
PR 填发布链接 → publish_status=已发布 + settlement_status=待核查
   ↓
PR 主管审核 approve → settlement_status=待付款 + 发 SettlementRequested 事件
   ↓ (同事务)
U05 监听 → 创建 settlement 记录 (DB UNIQUE 兜底)
   ↓
U05 settlement 状态推进 → mark_paid → promotion.settlement_status=已付款 (反向通知)
```

```
[PR] ──创建promotion──> PromotionService.create_promotion ──> DB
                                  │
                                  ├──> internal_code 序列号锁
                                  ├──> 快照 style/blogger 字段
                                  └──> 重复检测 warnings

[PR] ──填发布链接──> PromotionService.publish ──> DB
                                  │
                                  ├──> publish_status=已发布
                                  ├──> 同事务推 settlement_status=待核查
                                  └──> 发 PromotionPublished 事件 (U07 监听)

[PR 主管] ──审核 approve──> PromotionService.review ──> DB
                                  │
                                  ├──> settlement_status=待付款
                                  └──> 发 SettlementRequested 事件
                                            │
                                            └──> [U05 监听] ──> 创建 settlement
                                                  (同事务)
```

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 9 个 UC 覆盖 EP05-S02~S13 全部验收 | ✅ |
| internal_code 序列号防 race（行级锁 + 同事务） | ✅ |
| 快照填充逻辑明确（创建时一次性） | ✅ |
| 3 状态机转移 + 跨状态机校验完整 | ✅ |
| publish 同事务推进 settlement_status="待核查" | ✅ |
| review approve 仅发事件不直接创建 settlement | ✅ |
| 自审禁止（pr_id != reviewer.id） | ✅ |
| 列表查询用 CTE 计算 urge_status / dual_platform | ✅ |
| 字段权限脱敏 + audit 敏感值脱敏 | ✅ |
| 错误流程明确（每个 422/409/403 触发条件） | ✅ |
| update_like_count 内部 API 区分 crawler/user | ✅ |
| 端到端时序串联 U02/U03/U04/U05/U07 | ✅ |
