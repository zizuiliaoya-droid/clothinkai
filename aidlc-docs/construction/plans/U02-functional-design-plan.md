# U02 功能设计计划（Functional Design Plan）

> 单元：U02 — 商品 / SKU 基础  
> 阶段：MVP 第 2 个单元  
> 依赖：U01（认证 + 多租户 + 横切组件）

---

## 1. 单元上下文

### 1.1 覆盖故事
| 故事 | 阶段 | 说明 |
|---|---|---|
| EP02-S01 | MVP | 跟单创建款式 |
| EP02-S02 | MVP | 跟单创建 SKU |
| EP02-S03 | MVP | 编辑款式信息 |
| EP02-S04 | MVP | 编辑 SKU 成本/价格 |
| EP02-S05 | MVP | 按款式查询 SKU |
| EP02-S06 | MVP | 款号↔商品简称双向关联 |

注：EP02-S07（平台商品映射）属 V1 / U09 范畴；EP02-S08（套装）属 V2 / U17。本单元**不实现**。

### 1.2 覆盖代码
- `backend/app/modules/product/`（不含 PlatformProduct, Bundle）
  - `models.py`（Style, Sku, StyleAttachment 等 ORM）
  - `schemas.py`（Pydantic）
  - `repository.py`（StyleRepository, SkuRepository）
  - `domain.py`（业务方法）
  - `service.py`（StyleService, SkuService）
  - `api.py`（REST 端点）
  - `permissions.py`（操作 + 字段权限声明）
  - `exceptions.py`

### 1.3 与 U01 的接续关系
- 复用 `TenantScopedModel`（自动 tenant_id + RLS）
- 复用 `@audit` 装饰器 + AuditService
- 复用 `AttachmentService`（款式主图/详情图）
- 复用 `require_permission` 装饰器
- 字段级权限**不在 U02 落地**（U09 才完整支持），U02 先按"模块/功能级权限"占位 cost_price 等敏感字段

### 1.4 设计阶段后产出文档
- `aidlc-docs/construction/U02/functional-design/domain-entities.md`
- `aidlc-docs/construction/U02/functional-design/business-rules.md`
- `aidlc-docs/construction/U02/functional-design/business-logic-model.md`

---

## 2. 计划步骤

### Step 1 — 确认范围与依赖（仅 U02 范围；不含 PlatformProduct/Bundle）
- [x] 1.1 读取 unit-of-work.md 中 U02 定义
- [x] 1.2 读取 stories.md 中 EP02-S01~S06 验收标准
- [x] 1.3 读取 shared-infrastructure.md 与 U01 已实现组件
- [x] 1.4 标记 V1/V2 范围（EP02-S07/S08）不在本单元

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出本单元覆盖故事
- [x] 2.2 列出问题分类与具体问题
- [x] 2.3 等待用户填答 [Answer] 标签

### Step 3 — 生成 domain-entities.md
- [x] 3.1 Style 实体字段表 + 关系
- [x] 3.2 Sku 实体字段表 + 关系
- [x] 3.3 Brand / Category 是否独立实体
- [x] 3.4 StyleAttachment 关系（主图 + 详情图列表）
- [x] 3.5 ER 图（Mermaid）
- [x] 3.6 与 U01 实体的关系（tenant_id 外键，不直接关联 user 表）

### Step 4 — 生成 business-rules.md
- [x] 4.1 唯一性规则（style_code、sku_code 租户内唯一）
- [x] 4.2 必填规则（style_id、style_code、sku_code、color、size 等）
- [x] 4.3 引用完整性（sku.style_id 必须存在）
- [x] 4.4 价格 / 数值精度规则
- [x] 4.5 状态规则（design_status 默认值与流转占位 → U10a 接管）
- [x] 4.6 软删 / 硬删策略
- [x] 4.7 编辑触发审计的条件（变更字段才写 audit）
- [x] 4.8 字段级权限占位（U02 仅模块级，U09 落细）
- [x] 4.9 款号↔商品简称匹配规则（精确 / 模糊算法）

### Step 5 — 生成 business-logic-model.md
- [x] 5.1 创建款式 Use Case 流程（数据流 + 验证 + 持久化 + 审计）
- [x] 5.2 创建 SKU Use Case 流程
- [x] 5.3 编辑款式 / 编辑 SKU 流程（含字段对比与审计）
- [x] 5.4 按款式查询 SKU 流程（分页 + 排序）
- [x] 5.5 款号↔商品简称双向关联流程（精确 + 模糊回退）
- [x] 5.6 错误处理矩阵（409/422/403 各场景）

### Step 6 — 提交完成消息 + 等待审批
- [x] 6.1 展示 "🔧 Functional Design Complete - U02"
- [x] 6.2 等待用户 P1/P2 反馈或批准
- [x] 6.3 批准后写入 audit.md

---

## 3. 澄清问题（请填 [Answer]）

> 以下问题影响 domain-entities.md / business-rules.md / business-logic-model.md 的具体字段与算法。U02 是 MVP 数据底座，后续 11 个单元都直接依赖，**字段一旦定下后续修改成本高**。请尽量明确填答。

### 3.1 Style（款式）字段范围

**Q1**：Style 表除了 `style_code, style_name, brand, category` 之外，MVP 还需要哪些字段？请勾选并补充：

- [ ] **A. 标签（tags）** — 数组型自由标签（如 "夏季", "白搭"）
- [ ] **B. 季节（season）** — 枚举：春 / 夏 / 秋 / 冬 / 四季
- [ ] **C. 性别（gender）** — 枚举：女 / 男 / 中性 / 童
- [ ] **D. 主图（main_image）** — 通过 attachment_id 关联
- [ ] **E. 详情图（detail_images）** — 多张，列表顺序敏感
- [ ] **F. 备注（remark）** — 长文本
- [ ] **G. 创建人 / 跟单负责人（owner_id）** — 关联 user
- [ ] **H. 货期（lead_time_days）** — 整数天数
- [ ] **I. 设计状态（design_status）** — 见 Q3
- [ ] **J. 其他（请填写）**：

[Answer]: ABCDFGI 全选；其他保留 K.tag_color（数组，记录款式整体色系）；不要 H 货期（U10a 跟单字段）

**Q2**：Style.brand 和 Style.category 应该是：

- [ ] **A. 自由字符串**（前端可输任意值）
- [ ] **B. 枚举**（后端代码硬编码列表）
- [ ] **C. 字典表**（独立 brand / category 表，租户可维护）
- [ ] **D. brand 用字典表，category 用枚举**

[Answer]: D — brand 用字典表（每个租户可自维护品牌列表），category 用枚举（女装大类硬编码：连衣裙/上衣/裤装/裙装/外套/套装/配饰，可后续在 U09 改为字典表）

**Q3**：design_status 在 U02 阶段的取值与默认值？后续 U10a（V1）会扩展。MVP 阶段是否需要状态机？

- [ ] **A. 仅 2 个值：`大货` / `设计中`**，不做状态机，仅作字段
- [ ] **B. 仅 1 个默认值 `大货`**，所有 MVP 创建的都直接是大货
- [ ] **C. 7 个完整状态**（设计中/打版中/工艺中/核价中/打样中/确认中/大货），U02 就建状态机
- [ ] **D. 其他**

[Answer]: A — 仅 2 个值（大货 / 设计中），新建默认 `大货`（MVP 跟单直接录入大货款），U10a 引入完整 7 状态状态机时再改造

### 3.2 Sku（SKU）字段范围

**Q4**：Sku 表除了 `style_id, sku_code, color, size, cost_price` 之外，MVP 还需要哪些字段？

- [ ] **A. 采购价（purchase_price）** — 与 cost_price 区别？
- [ ] **B. 基本售价（base_price / list_price）**
- [ ] **C. 重量（weight_kg）**
- [ ] **D. 体积（volume_cm3）**
- [ ] **E. 条形码（barcode）** — 唯一约束？
- [ ] **F. 库存数量（stock_qty）** — U02 是否包含库存模块？
- [ ] **G. 启用状态（is_active）** — 软停用 SKU
- [ ] **H. 平台 SKU 映射（platform_sku_id）** — V1 / U09 范围，不要
- [ ] **I. 其他（请填写）**：

[Answer]: ABG（采购价/基本售价/启用状态）；不要 CDEF；库存不在本系统范围（线下）

> 关于 A：cost_price = 我方制造成本（含面料/工艺/工费），purchase_price = 我方向工厂的采购价（外采款）。两者互斥使用：自产款用 cost_price，外采款用 purchase_price。是否同意？  
> [Answer]: 同意，但允许并存（两个字段都可有值，由 SKU 类型决定哪个生效）；新增字段 `sourcing_type` 枚举（自产 / 外采 / 混合），表示该 SKU 的来源类型

**Q5**：cost_price / purchase_price / base_price 的精度？数据库 DECIMAL(p, s)：

- [ ] **A. DECIMAL(10, 2)** — 最大 99,999,999.99，精度到分
- [ ] **B. DECIMAL(12, 2)** — 最大 9,999,999,999.99
- [ ] **C. DECIMAL(10, 4)** — 4 位小数（行业用于 ERP）
- [ ] **D. 其他**

[Answer]: A — DECIMAL(10, 2)，精度到分，服装电商单价 9 位整数足够（最贵货品也不会到千万元）

**Q6**：color 和 size 是自由字符串还是受控字典？

- [ ] **A. 都自由字符串**（前端任意输入）
- [ ] **B. 都字典表**（租户可维护）
- [ ] **C. color 自由字符串，size 字典表**
- [ ] **D. color 字典表，size 自由字符串**

[Answer]: A — 都自由字符串（U02 阶段简化），U09/V1 视实际需要再升级为字典表

**Q7**：SKU 删除策略？

- [ ] **A. 硬删除**（DELETE 物理删）
- [ ] **B. 软删除**（is_deleted=true，保留历史）
- [ ] **C. 无删除**（仅停用 is_active=false）
- [ ] **D. 软删除 + 引用检查**（被 promotion / order 引用过的不能删，仅停用）

[Answer]: D — 软删 + 引用检查；未被任何 promotion/order/import 引用过 → 允许软删（is_deleted=true）；已被引用 → 仅允许停用（is_active=false），返回 409 错误并提示

### 3.3 关系与级联

**Q8**：删除 Style 时，其下的 SKU 如何处理？

- [ ] **A. 级联软删**（style 软删 → sku 全部软删）
- [ ] **B. 不允许删 style**（必须先停用所有 SKU）
- [ ] **C. 仅停用 style**（is_active=false），SKU 自动失效
- [ ] **D. 其他**

[Answer]: B — 不允许直接删 style；必须先把全部 SKU 处理（停用或删除）后才能删 style；或者管理员可在前端 "停用款式" 同时勾选 "停用所有 SKU" 一并操作

**Q9**：Style ↔ Sku 关系是否支持"无 SKU 的 Style"？

- [ ] **A. 必须**（Style 创建时立即至少 1 个 SKU）
- [ ] **B. 可选**（Style 可独立存在，0 个 SKU 也合法）
- [ ] **C. 可选但用户提醒**（无 SKU 的款式列表显示警告标签）

[Answer]: B — 可选；MVP 期间设计师/跟单常常先建 style 再陆续录 SKU；EP02-S01 验收明确"创建款式后续创建 SKU"，独立创建合法

### 3.4 Brand / Category 字典表

**Q10**：Brand 字典表的字段？

[Answer]: 必备字段：id (UUID) / tenant_id / brand_code (租户内唯一) / brand_name (展示名) / is_active / created_at / updated_at；不需要 brand_logo 等扩展字段（MVP 简化）

**Q11**：Category 既然 MVP 用枚举，就不建表？U09 切字典表时再做迁移？

[Answer]: 是，MVP 用 Python Enum 硬编码 7 个值；后续切字典表通过 alembic migration（U09 阶段）

### 3.5 款号↔商品简称双向关联（EP02-S06）

**Q12**：商品简称 = 哪个字段？

- [ ] **A. style_name 直接用作商品简称**（无独立字段）
- [ ] **B. Style 表新增 short_name 字段**（独立简称，如 style_name="波点花边长袖针织连衣裙"，short_name="波点花边长袖"）
- [ ] **C. 简称 = style_name 的前 N 个字符**

[Answer]: B — 新增 short_name 字段；MVP 期间 short_name 选填（不填则前端展示 style_name）；推广录入时优先用 short_name 自动填充

**Q13**：模糊匹配算法（按商品简称反查款号）？

- [ ] **A. ILIKE %keyword%** —— 简单 SQL 子串匹配
- [ ] **B. PostgreSQL 全文搜索（pg_trgm 或 tsvector）**
- [ ] **C. 前端搜索（前端取全部 style 列表，本地 fuzzy）** — 数据量大时不行
- [ ] **D. 其他**

[Answer]: A — ILIKE %keyword%（MVP 简化）；同时按 style_code、style_name、short_name 三个字段做 OR 匹配；返回 top 20 候选；后续 U09 / V1 视性能再升级 pg_trgm

**Q14**：当 PR 输入的款号在系统中不存在，前端如何处理？

- [ ] **A. 阻塞**（不允许继续录入推广）
- [ ] **B. 警告但允许继续**（记下输入的款号字符串，作为外部款号，后续手动关联）
- [ ] **C. 跳转新建款式弹窗**

[Answer]: B — 警告但允许继续；该 promotion 的 style_code 字段记录原始输入字符串，style_id 留 NULL；后续跟单查到 unmatched promotion 列表手动补关联（或忽略）

### 3.6 字段级权限占位

**Q15**：cost_price / purchase_price / base_price 在 U02 阶段如何处理权限？U09 才有字段级权限。

- [ ] **A. 仅模块级**（凡有 product:read 权限就能看所有字段，含成本价）
- [ ] **B. 端点级拆分**（GET /skus/{id} 不返回 cost_price，需调用 GET /skus/{id}/cost-price 单独获取，权限分别控制）
- [ ] **C. 角色级 hardcode**（在 service 层硬编码：跟单和管理员能看，PR/设计师/财务不能看）
- [ ] **D. 其他**

[Answer]: C + 标记 — service 层硬编码角色判断（跟单 / 管理员 / 财务可见 cost_price/purchase_price，PR/设计师不可见，base_price 全可见），但在 schemas.py 中加 `# TODO U09: 改为字段级权限` 注释；U09 落地后清理硬编码

### 3.7 审计触发条件

**Q16**：编辑款式时，哪些字段变更要写 audit_log？

- [ ] **A. 所有字段变更都写**
- [ ] **B. 仅敏感字段变更写**（cost_price, purchase_price, base_price, style_code）
- [ ] **C. 仅价格字段写**

[Answer]: B — 仅敏感字段变更写 audit；普通字段（style_name, short_name, tags, season, brand, category, season, gender, remark）变更不写 audit（避免日志噪音）；EP02-S03 验收 "字段值未变更不写" 仍然适用

### 3.8 接口分页

**Q17**：style 列表接口分页参数？默认页大小？

[Answer]: page 从 1 开始 / page_size 默认 20，最大 100；按 created_at DESC 默认排序；支持按 style_code/style_name/short_name 关键字搜索（ILIKE）；支持按 brand_id / category / is_active 筛选

### 3.9 衍生计算 / 报表口径前置

**Q18**：U14（投产报表，V1）会用 cost_price 做"投入产出"分析。U02 是否需要冗余字段加速查询？

- [ ] **A. 不冗余**（U14 时 JOIN sku 表实时查 cost_price）
- [ ] **B. promotion / order 表创建时快照 cost_price**（避免历史价格变更影响历史报表）

[Answer]: B — 但放在 U04（promotion）和 U16（order）单元实施；U02 仅保证 cost_price 字段存在 + 可查；快照逻辑在引用方 done

---

## 4. 决策摘要（在用户填答后由 AI 整理）

> 此处在用户回复"填好了"之后，AI 总结所有 [Answer] 形成 12-15 条最终决策清单，作为 domain-entities.md / business-rules.md / business-logic-model.md 的输入。
