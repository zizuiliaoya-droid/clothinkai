# U03 功能设计计划（Functional Design Plan）

> 单元：U03 — 博主库基础  
> 阶段：MVP 第 3 个单元  
> 依赖：U01（认证 + 多租户 + 横切）；与 U02 完全解耦

---

## 1. 单元上下文

### 1.1 覆盖故事
| 故事 | 阶段 | 说明 |
|---|---|---|
| EP04-S01 | MVP | PR 添加博主（xiaohongshu_id 唯一） |
| EP04-S02 | MVP | PR 编辑博主信息（报价为敏感字段必记 audit） |
| EP04-S03 | MVP | 博主搜索与筛选（昵称 / 类型 / 粉丝量 / 质量标签） |

排除范围：
- EP04-S04 系统计算博主类型（V1 / U10b BloggerTagService）
- EP04-S05+ 智能博主标签（V1 / U10b）

### 1.2 覆盖代码
- `backend/app/modules/blogger/`（不含 BloggerTagService）
  - `models.py`（Blogger ORM）
  - `enums.py`（BloggerType / Platform）
  - `schemas.py`（Pydantic）
  - `repository.py`（BloggerRepository）
  - `domain.py`（业务规则验证 + audit）
  - `service.py`（BloggerService）
  - `permissions.py`（blogger:* 权限字符串）
  - `legacy_field_permissions.py`（QUOTE_VISIBLE_ROLES，U09 清理）
  - `exceptions.py`
  - `api.py`（REST 端点）
  - `deps.py`（FastAPI 依赖注入）

### 1.3 与 U01 / U02 的接续关系
- 完全继承 U01（TenantScopedModel / @audit / @require_permission / RLS）
- 与 U02 解耦（不共享表 / 不共享 service）；后续 U04 才合并使用
- 复用 U02 已建立的"字段级权限硬编码过渡"模式（PRICE_VISIBLE_ROLES → 改为 QUOTE_VISIBLE_ROLES）

### 1.4 设计阶段后产出文档
- `aidlc-docs/construction/U03/functional-design/domain-entities.md`
- `aidlc-docs/construction/U03/functional-design/business-rules.md`
- `aidlc-docs/construction/U03/functional-design/business-logic-model.md`

---

## 2. 计划步骤

### Step 1 — 确认范围
- [x] 1.1 读取 unit-of-work.md U03 定义
- [x] 1.2 读取 stories.md EP04-S01~S03 完整验收
- [x] 1.3 标记 V1 范围（EP04-S04+ / BloggerTagService 不在本单元）

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出覆盖故事
- [x] 2.2 列出问题分类
- [x] 2.3 等待用户填答 [Answer] 标签

### Step 3 — 生成 domain-entities.md
- [x] 3.1 Blogger 实体字段表 + 索引
- [x] 3.2 ER 图（Mermaid）
- [x] 3.3 4 个 Python Enum（BloggerType / Platform / GenderTarget / QualityTag）
- [x] 3.4 与 U01 实体的关系
- [x] 3.5 演化路线（U10b 加 BloggerTagService / U04 引用）

### Step 4 — 生成 business-rules.md
- [x] 4.1 唯一性（xiaohongshu_id 租户内唯一）
- [x] 4.2 必填规则
- [x] 4.3 数值规则（follower_count / quote 精度）
- [x] 4.4 软删 / 引用检查（U04/U10b 后向扩展）
- [x] 4.5 编辑触发审计的字段（报价 / xiaohongshu_id 等）
- [x] 4.6 字段级权限占位（QUOTE_VISIBLE_ROLES）
- [x] 4.7 搜索筛选规则
- [x] 4.8 错误码矩阵

### Step 5 — 生成 business-logic-model.md
- [x] 5.1 创建博主 UC（含重复 xiaohongshu_id 提示语义）
- [x] 5.2 编辑博主 UC（含 quote 字段权限 + 敏感字段 audit）
- [x] 5.3 搜索博主 UC（多筛选条件 + 字段过滤）
- [x] 5.4 软删 / 停用 UC

### Step 6 — 提交完成消息 + 等待审批
- [x] 6.1 展示 "🔧 Functional Design Complete - U03"
- [x] 6.2 等待用户 P1/P2 反馈或批准

---

## 3. 澄清问题（请填 [Answer]）

> 每问预填合理默认值，作答即代表确认。U03 比 U02 简单很多（仅 CRUD + 搜索，无状态机 / upsert / 模糊搜索）。

### 3.1 Blogger 字段范围

**Q1**：Blogger 表 MVP 字段范围？

[Answer]: 字段：
- `id` (UUID PK)
- `tenant_id` (UUID FK)
- `xiaohongshu_id` (VARCHAR(64))：小红书账号 ID（业务键）
- `nickname` (VARCHAR(128))：昵称
- `platform` (VARCHAR(16))：平台枚举（默认"小红书"）
- `wechat` (VARCHAR(64))：微信号（可选，敏感）
- `phone` (VARCHAR(32))：手机号（可选，敏感）
- `follower_count` (INT)：粉丝量（可选，整数）
- `blogger_type` (VARCHAR(16))：博主类型枚举（素人/KOC/KOL/明星，U02 阶段由 PR 手填，V1 改自动计算）
- `gender_target` (VARCHAR(16))：博主受众性别（女性/男性/中性，可选）
- `category_tags` (JSONB array)：内容类目标签（如["穿搭","美妆"]，可选）
- `quality_tags` (JSONB array)：质量标签（如 ["高互动","真实粉丝","已合作"]，可选；U02 阶段 PR 手填，U10b 自动计算）
- `quote` (DECIMAL(10,2))：合作报价（敏感字段，按角色屏蔽）
- `cooperation_history` (TEXT)：合作历史备注
- `remark` (TEXT)：备注
- `is_suspected_fake` (BOOLEAN)：假号嫌疑标记（U02 阶段 PR 手标，V1 自动计算）
- `is_active` (BOOLEAN)
- `is_deleted` (BOOLEAN)
- `created_at` / `updated_at`

**Q2**：xiaohongshu_id 是否必填？是否需要其他平台支持？

[Answer]: xiaohongshu_id 必填（业务核心场景）；platform 字段默认 `"小红书"`，预留枚举支持后续扩展（"抖音"/"快手"/"B站"），但 V1 之前不实施跨平台逻辑

**Q3**：blogger_type 取值？

[Answer]: 4 个枚举：素人 / KOC / KOL / 明星
- MVP：PR 手动选择
- V1（U10b）：系统按粉丝量自动计算（粉丝 < 1k = 素人 / 1k-10k = KOC / 10k-100w = KOL / 100w+ = 明星）

**Q4**：quote 精度？

[Answer]: DECIMAL(10,2)（与 U02 一致，最大 99,999,999.99，到分），CHECK ≥ 0

### 3.2 唯一性

**Q5**：xiaohongshu_id 唯一约束作用域？

[Answer]: `UNIQUE (tenant_id, xiaohongshu_id) WHERE is_deleted = false`（部分唯一，软删后释放，与 U02 风格一致）

**Q6**：重复创建时，前端是否需要"该博主已存在，是否查看？"的引导？

[Answer]: 是 — service 返回 409 时 details 含 `existing_blogger_id`，前端可根据此跳转到博主详情页查看，详见 EP04-S01.given2 验收

### 3.3 字段级权限（U02 模式延续）

**Q7**：quote / wechat / phone 字段级权限？

[Answer]:
- `quote`：仅 admin / pr / pr_manager / finance 可见；其他角色（设计师 / 跟单 / 运营）不可见
- `wechat`：admin / pr / pr_manager 可见
- `phone`：admin / pr / pr_manager 可见
- 实施方式与 U02 一致：`modules/blogger/legacy_field_permissions.py` 内定义 `QUOTE_VISIBLE_ROLES` / `CONTACT_VISIBLE_ROLES`，service 层 `_to_response` 按角色过滤；带 TODO U09 注释

**Q8**：quote 写权限？

[Answer]: 仅 admin / pr / pr_manager 可写（与读权限一致；finance 仅读不写）；其他角色 PUT 含 quote → 403 `FIELD_PERMISSION_DENIED`

### 3.4 审计字段

**Q9**：编辑博主时，哪些字段变更要写 audit_log？

[Answer]:
- **写 audit + 真实 before/after**：`xiaohongshu_id`, `nickname`
- **写 audit + 脱敏标记**：`quote_changed: true`, `wechat_changed: true`, `phone_changed: true`（与 U02 SKU 价格脱敏一致，audit_log 不存历史值）
- **不写 audit**：`category_tags`, `quality_tags`, `remark`, `cooperation_history`, `blogger_type`, `gender_target`, `follower_count`, `is_suspected_fake`, `is_active`, `platform`

**Q10**：博主创建时是否要写 audit？

[Answer]: 是 — 写 `blogger.create` action，仅记 `xiaohongshu_id` + `nickname`（非敏感字段），与 U02 style.create 风格一致

### 3.5 删除策略

**Q11**：博主删除策略？

[Answer]: 软删 + 引用检查（与 U02 SKU 一致）：
- service 提供 `check_references()` 接口，U03 阶段 promotion 表不存在返回 0
- TODO U04: 改为查询 promotion 表是否引用了该 blogger_id
- 已被引用 → 仅停用（is_active=false）
- 未被引用 → 允许软删

### 3.6 搜索筛选

**Q12**：搜索接口的查询参数和组合逻辑？

[Answer]: `GET /api/bloggers/search` 支持参数：
- `keyword` — ILIKE 模糊匹配 nickname / xiaohongshu_id / wechat（OR 关系）
- `blogger_type` — 精确匹配枚举
- `follower_count_min` / `follower_count_max` — 范围筛选
- `quality_tag` — JSONB 包含查询（`quality_tags @> '["high_interaction"]'::jsonb`）
- `category_tag` — JSONB 包含查询
- `platform` — 精确匹配（默认不过滤）
- `is_active` (默认 true) / `include_inactive` (默认 false)
- `is_suspected_fake` (可选)
- 分页：`page` / `page_size`（默认 1/20，max 100）
- 排序：`order_by`，默认 `follower_count DESC`，可选 `created_at DESC`

**Q13**：搜索的性能预期？

[Answer]: 与 U02 类似 — 单租户最多 1763+ 博主（按业务文档），P95 ≤ 200ms；不需要 GIN trgm 索引（数量级远小于 style 表的 5 万）；ILIKE + (tenant_id, is_active) B-tree 索引足够

**Q14**：JSONB tags 是否需要 GIN 索引？

[Answer]: 是 — `category_tags` 和 `quality_tags` 加 GIN 索引（`USING gin`），支撑按 tag 筛选 P95 ≤ 100ms；表规模虽小但 tag 查询频次较高（PR 选博主主要靠 tag）

### 3.7 列表分页与字段过滤

**Q15**：博主列表是否使用同一接口（GET /api/bloggers/）和搜索接口？

[Answer]: 合并为一个接口 `GET /api/bloggers/`，所有筛选条件都通过 query 参数传递；`GET /api/bloggers/search` 不单独实现（可在 router 上加 alias 但实际指向同一处理函数）

### 3.8 数据迁移

**Q16**：1763+ 博主历史数据如何导入？

[Answer]: 不在 U03 阶段实施。MVP 启用后由 U06c（博主导入适配器）通过 Excel 模板批量导入；U03 提供 `BloggerService.upsert_by_xiaohongshu_id()` 内部 API 供 U06c 调用（不暴露 HTTP），与 U02 SkuService.upsert_sku 模式完全一致（数据库原子 ON CONFLICT + 复用同一套校验/权限/审计）

### 3.9 ID 标识

**Q17**：blogger 是否需要业务 ID 显示给用户（如 BL00001）？

[Answer]: 不需要；UUID + xiaohongshu_id 已足够；前端展示用 nickname + xiaohongshu_id 作为身份标识

---

## 4. 决策摘要（用户填答后由 AI 整理）

> 用户回复"继续"后，AI 总结 [Answer] 形成最终决策清单，作为 domain-entities.md / business-rules.md / business-logic-model.md 的输入。
