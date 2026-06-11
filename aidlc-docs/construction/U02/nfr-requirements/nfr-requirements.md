# U02 非功能需求（NFR Requirements）

> 单元：U02 — 商品 / SKU 基础  
> 范围：U02 特异性 NFR；通用 NFR 全部继承 `aidlc-docs/construction/U01/nfr-requirements/nfr-requirements.md`  
> 阅读：先读 U01 通用基线，再看本文件增量

---

## 1. 与 U01 NFR 基线的关系

### 1.1 完全继承（不重复定义）
| 维度 | U01 已定义 | U02 继承说明 |
|---|---|---|
| 错误码体系 | `{code, message, details}` | U02 沿用，新增 8 个 product 错误码 |
| 安全（认证） | JWT + password_changed_at + 黑名单 | U02 全部继承 |
| 安全（授权） | RBAC + permission 装饰器 | U02 沿用 + 新增 `product:*`, `brand:*` 权限 |
| 安全（多租户） | ORM 注入 + RLS 双重保护 | U02 沿用，所有新表继承 `TenantScopedModel` |
| 审计 | append-only + @audit 装饰器 | U02 沿用，新增 5 个 action 名 |
| 速率限制 | slowapi IP + Redis username + DB 锁定 | U02 全 API 默认应用通用限流 |
| 监控 | Prometheus + Sentry + Loki | U02 沿用 + 新增 module=product tag |
| 健康检查 | `/health` + `/ready` | U02 不新增，依赖一致 |
| 备份 | daily/monthly tar.gz to R2 | 新表自动纳入备份范围 |

### 1.2 U02 增量
| 维度 | 增量 | 落地位置 |
|---|---|---|
| 容量 | 单租户 5 万 style / 50 万 sku | §2 |
| 性能 SLA | match 模糊 P95 ≤ 300ms / list P95 ≤ 200ms / 写 P95 ≤ 200ms | §3 |
| 索引 | pg_trgm GIN（U02 强制） | §3.4 |
| 字段权限 | cost_price 三角色硬编码 + TODO U09 | §4 |
| 演化兼容 | design_status / sourcing_type / 快照接口 | §6 |
| 监控指标 | Prometheus product handler P95 | §5 |
| 数据迁移 | final.xlsx 字段参考、不做历史数据迁移 | §7 |

---

## 2. 容量需求（Scalability）

### 2.1 数据规模（峰值预期 / 单租户）

| 表 | MVP 上限 | V1 上限 | V2+ 上限 |
|---|---|---|---|
| `style` | 5 万行 | 10 万行 | 50 万行（触发升级评估） |
| `sku` | 50 万行 | 100 万行 | 500 万行 |
| `brand` | 200 行 | 500 行 | 1000 行 |
| `style_detail_image` | 50 万行（每款均 10 张） | 100 万行 | 500 万行 |

### 2.2 并发负载（API QPS）

| API | 平均 QPS | 峰值 QPS | 触发场景 |
|---|---|---|---|
| `GET /api/styles/` 列表 | 5 | 50 | PR / 跟单浏览页面 |
| `GET /api/styles/{id}` 详情 | 10 | 80 | 详情页打开 |
| `GET /api/skus/by-style/{id}` | 5 | 30 | 推广录入选 SKU |
| `GET /api/styles/match` | 20 | 100 | PR 集中录推广时段 |
| `POST /api/styles/` | 1 | 5 | 跟单批量录入 |
| `POST /api/skus/` | 5 | 20 | 跟单批量录 SKU |
| `PUT /api/skus/{id}` | 2 | 10 | 跟单调价 |

### 2.3 增长触发器
- 单租户 `style` 突破 5 万行 → P95 监控连续 1 周 > 500ms 触发：
  1. 检查 style_search_trgm 索引使用率
  2. 评估升级到 PostgreSQL 全文搜索（tsvector + GIN）或 Elasticsearch
- `sku` 突破 50 万行 → 评估按 tenant_id 分区（PostgreSQL Declarative Partitioning）

---

## 3. 性能需求（Performance）

### 3.1 SLA 总表

| API | P50 | P95 | P99 | 超时 |
|---|---|---|---|---|
| `GET /api/styles/` 列表（默认页 20） | ≤ 50ms | ≤ 200ms | ≤ 500ms | 3s |
| `GET /api/styles/` 列表（page_size=100） | ≤ 100ms | ≤ 600ms | ≤ 1s | 3s |
| `GET /api/styles/{id}` 详情 | ≤ 30ms | ≤ 100ms | ≤ 300ms | 3s |
| `GET /api/skus/by-style/{id}` | ≤ 30ms | ≤ 100ms | ≤ 300ms | 3s |
| `GET /api/styles/match?style_code=` 精确 | ≤ 10ms | ≤ 50ms | ≤ 100ms | 1s |
| `GET /api/styles/match?keyword=` 模糊 | ≤ 80ms | ≤ 300ms | ≤ 500ms | 3s |
| `POST /api/styles/` 创建 | ≤ 50ms | ≤ 150ms | ≤ 400ms | 5s |
| `POST /api/skus/` 创建 | ≤ 50ms | ≤ 150ms | ≤ 400ms | 5s |
| `PUT /api/styles/{id}` 编辑 | ≤ 80ms | ≤ 200ms | ≤ 500ms | 5s |
| `PUT /api/skus/{id}` 编辑 | ≤ 80ms | ≤ 200ms | ≤ 500ms | 5s |
| `DELETE /api/styles/{id}` | ≤ 80ms | ≤ 200ms | ≤ 500ms | 5s |

### 3.2 SLA 适用条件
- 测试基准：5 万 style + 50 万 sku（mock 数据）
- 5 万行容量基础上指标达标
- 单租户独立测试，不受其他租户影响（依赖 RLS + 共享连接池）
- 不含网络往返（应用 ↔ DB ↔ Redis 内部时延）

### 3.3 监控数据源
- **Prometheus** = SLA 真实数据源（P50/P95/P99 真实分位数）
  - 指标：`http_request_duration_seconds{handler="/api/styles/match",method="GET"}`
  - 查询：`histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))`
- **Sentry** = 异常 + 慢事务抽样（traces_sample_rate=0.1）
  - 不作为 SLA 主数据源（抽样不准）
  - 触发条件：单次请求超 1s → 自动捕获到 transaction
- 告警：
  - Prometheus alertmanager：列表 P95 > 1s 持续 5min → SRE 告警
  - Sentry：5xx 错误率 > 5% → 即时告警

### 3.4 索引必建项（U02 alembic migration 强制）
```sql
-- 列表 / 筛选
CREATE INDEX idx_style_tenant_active ON style (tenant_id, is_active, is_deleted);
CREATE INDEX idx_style_brand ON style (tenant_id, brand_id);
CREATE INDEX idx_style_category ON style (tenant_id, category);
CREATE INDEX idx_sku_tenant_style ON sku (tenant_id, style_id);
CREATE INDEX idx_sku_tenant_active ON sku (tenant_id, is_active, is_deleted);

-- 唯一约束（部分索引，软删后释放）
CREATE UNIQUE INDEX uq_style_code ON style (tenant_id, style_code) WHERE is_deleted = false;
CREATE UNIQUE INDEX uq_sku_code ON sku (tenant_id, sku_code) WHERE is_deleted = false;
CREATE UNIQUE INDEX uq_brand_code ON brand (tenant_id, brand_code);

-- 模糊搜索（U02 强制）
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_style_search_trgm ON style
  USING gin (
    (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
    gin_trgm_ops
  ) WHERE is_deleted = false;
```

---

## 4. 安全需求（Security）

### 4.1 字段级权限（U02 过渡 / U09 落细）

#### 4.1.1 角色硬编码可见性矩阵
| 角色 | cost_price | purchase_price | base_price |
|---|---|---|---|
| 管理员 / 跟单 / 财务 | ✅ | ✅ | ✅ |
| 其他角色（PR / 设计师等） | ❌（None） | ❌（None） | ✅ |

#### 4.1.2 实施约束
- service 层执行字段过滤（`SkuService.to_response`）
- 所有过滤位置必须有 `# TODO U09: 改为字段级权限` 注释
- 写操作：PR 等无权角色 PUT 含 cost_price → 403 `FIELD_PERMISSION_DENIED`
- U09 完成后清理硬编码（一次性 grep 替换）

### 4.2 敏感字段不加密（明确威胁模型）

#### 4.2.1 决策
cost_price / purchase_price / base_price 在数据库存明文（DECIMAL）。

#### 4.2.2 威胁模型边界
- **本决策仅防御**：普通业务用户跨角色越权读取（PR/设计师/运营 不应看到 cost_price）
- **本决策不防御**：DBA / 运维 / 拥有数据库直接读权限的人查看明文（视为可信内部人员）
- **RLS 解决的是租户隔离**（租户 A 看不到租户 B），**不解决字段保密**（同租户的 DBA 仍可读所有字段）
- **应用层防护**：
  - `clothing_app` 角色（业务连接）通过 service 层 + Pydantic schema 字段过滤
  - DBA 用 `clothing_bypass`（应急专用，仅高级管理员持有）
- **审计**：所有 cost_price / purchase_price 变更全部进 audit_log

#### 4.2.3 演进选项
若 V2+ 出现合规要求或客户审计要求 → 引入 pgcrypto 字段级加密 + KMS 集成（独立单元承担，约 1-2 人周）。

### 4.3 输入验证

| 字段 | 长度限制 | 字符集 | 校验位置 |
|---|---|---|---|
| `style_code` | ≤ 64 | 字母 + 数字 + `-` + `_` | Pydantic + DB |
| `sku_code` | ≤ 64 | 同上 | Pydantic + DB |
| `style_name` | ≤ 255 | 任意（含中文） | Pydantic |
| `short_name` | ≤ 64 | 任意 | Pydantic |
| `color` | ≤ 64 | 任意 | Pydantic |
| `size` | ≤ 32 | 任意 | Pydantic |
| `cost_price` 等 | DECIMAL(10,2) ≥ 0 | 数字 | Pydantic + DB CHECK |
| `tags`, `tag_color` | 数组 ≤ 20 项 | 每项 ≤ 32 | Pydantic |

### 4.4 上传约束
继承 U01 AttachmentService：
- 类型白名单：jpg / jpeg / png / webp / avif
- 单文件 ≤ 5MB
- 单款式详情图 ≤ 10 张，总大小 ≤ 50MB
- ClamAV 病毒扫描（V1 优化，U02 不引入）

### 4.5 速率限制（继承 U01）
| 维度 | 阈值 | 说明 |
|---|---|---|
| IP | 60 req/min | slowapi IP key_func |
| 用户 | 600 req/min | slowapi user key_func |
| 写操作 | 30 req/min/用户 | 创建 / 编辑 / 删除 |

---

## 5. 监控与可观测性（Observability）

### 5.1 Prometheus 指标
| 指标 | 类型 | 标签 | 用途 |
|---|---|---|---|
| `http_request_duration_seconds` | histogram | handler, method, status | SLA P50/P95/P99 |
| `http_requests_total` | counter | handler, method, status | 流量 / 错误率 |
| `db_query_duration_seconds` | histogram | query_type | 慢查询分析（U02 不新增） |

### 5.2 Sentry
- transaction tag：`module=product`
- 异常捕获：所有未处理 5xx
- 慢事务抽样：traces_sample_rate=0.1（U01 配置，U02 沿用）

### 5.3 日志 + 审计敏感字段策略
- 继承 U01 structlog JSON 格式
- 关键字段：`tenant_id`, `actor_id`, `request_id`, `module=product`, `action=style.create|sku.update|...`
- **日志（structlog）**：不记录敏感字段值（cost_price / purchase_price） — 仅记录变更标记 `"cost_price_changed": true`
- **审计（audit_log 表）**：同样脱敏，敏感值字段仅写 `*_changed: true` 标记，不写真实数值
  - 例：`audit_log.changes = {"sku_code": {"before": "old", "after": "new"}, "cost_price_changed": true}`
  - 普通字段（sku_code / sourcing_type）正常记录 before/after
  - 敏感值字段（cost_price / purchase_price）只记标记
  - 与威胁模型一致：DBA 直查 sku 表能看到当前 cost_price，但 audit_log 表本身不存历史值
- base_price 全角色可见，按正常 before/after 记录

### 5.4 告警阈值

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| `histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/styles.*"}) > 1` 持续 5min | Prometheus alertmanager → 企微/邮件 | SRE |
| `rate(http_requests_total{handler=~"/api/styles.*",status=~"5.."}[5m]) > 0.05` | Sentry → 企微 | 后端 |
| audit_log 写入失败 | Sentry → 即时 | 后端 leader |

---

## 6. 演化兼容性（与后续单元的契约）

### 6.1 字段级权限演化（U09）
- U02 service.py 中所有 `# TODO U09` 标记位置
- U09 阶段一次性切换为基于 `Permission.field_filter()` 动态过滤

### 6.2 设计状态演化（U10a）
- `design_status VARCHAR(16)` 字段不变
- U10a 直接扩展 `DesignStatus` Python Enum：增加 5 个新值（设计中已存在 / 大货已存在 / 新增打版中、工艺中、核价中、打样中、确认中）
- 同时新建 `design_workflow` 子表（U10a 范围）

### 6.3 价格快照演化（U04 / U16）
- U02 仅保证 `Sku.cost_price`, `Sku.base_price` 字段稳定可读
- U04 在 `promotion` 表创建时**应用层快照**：
  ```python
  # U04 PromotionService.create
  sku = await sku_service.get_for_snapshot(sku_id)  # U02 提供
  promotion.cost_price_snapshot = sku.cost_price
  promotion.base_price_snapshot = sku.base_price
  ```
- U16 在 `order` 表同理快照

### 6.4 PlatformProduct 演化（U09）
- U02 不创建 PlatformProduct 表
- U09 新建 platform_product 表 + UNIQUE (tenant_id, platform, platform_id)
- 引用 sku.id（U02 字段不变）

### 6.5 套装演化（U17）
- U02 不创建 Bundle / BundleItem
- U17 新建 bundle 表 + bundle_item 子表，引用 sku.id

### 6.6 字典表演化（U09）
- `category` 当前 Python Enum
- U09 新建 `category` 字典表，alembic migration 数据迁移
- service 层从 Enum 校验改为字典表 FK 校验

---

## 7. 数据迁移 & 演进

### 7.1 final.xlsx 数据迁移决策
- **不做历史数据完整迁移**（与 INCEPTION 阶段决策一致）
- final.xlsx 仅作为字段对照参考（管理员手册）
- MVP 阶段租户启用系统后：
  - 单条录入：跟单使用 UI 录入 style + sku
  - 批量录入：U06b 启用后通过 Excel 模板上传（依赖 U02 的 `SkuService.upsert_by_code`）
- U02 提供 0 数据起步能力

### 7.2 Alembic Migration 执行（与 U01 Q11=B 决策一致）
- **专用 migration job**，不通过 Zeabur 滚动部署内联
- 流程：
  1. PR 合并到 main
  2. 手动触发 `.github/workflows/migrate.yml`（已 U01 建立）
  3. job 执行 `alembic upgrade head` → 验证 staging 数据 → 触发 prod migration
  4. migration 成功 → 触发 `deploy-prod.yml` 部署应用
  5. 失败 → alembic 自动回滚 → 应用层不部署
- U02 单次 migration 操作：
  - 创建 4 张表（style / sku / brand / style_detail_image）
  - 创建 12 个索引（含 GIN trgm）
  - 启用 `pg_trgm` 扩展
  - 启用 RLS 策略（4 张表）
  - 数据初始化：每租户预置 0 个 style（首次部署不填入示例数据）

---

## 8. 可恢复性（Recoverability）

继承 U01 备份框架：
- 每天 03:30 全量备份（pg_dump → R2 backups/daily/）
- 每月 1 日 04:00 月度备份
- 30 天 daily + 12 月 monthly 保留策略
- U02 4 张新表自动纳入（无需配置）
- 恢复演练：通过 `backend/scripts/restore_backup.py`

---

## 9. 测试覆盖需求

### 9.1 覆盖率门槛

| 文件 | 最低覆盖率 | 说明 |
|---|---|---|
| `service.py` | 80% | 核心业务逻辑 |
| `repository.py` | 70% | CRUD + 关键查询 |
| `domain.py` | 90% | 业务规则 BR-U02-NN 全覆盖 |
| `api.py` | 60% | 端到端请求/响应/错误码 |

### 9.2 必须覆盖的集成测试场景

| # | 场景 | 验收映射 |
|---|---|---|
| 1 | 创建 style + 重复 style_code → 409 | EP02-S01 |
| 2 | 创建 sku + style_id 不存在 → 422 | EP02-S02 |
| 3 | 创建 sku + sku_code 重复 → 409 | EP02-S02 |
| 4 | 编辑 cost_price + 跟单角色 → 200 + audit | EP02-S04 |
| 5 | 编辑 cost_price + 设计师角色 → 403 | EP02-S04 |
| 6 | 按款式查 sku，6 个 sku → 返回 6 项 | EP02-S05 |
| 7 | 按款式查 sku，0 个 sku → 200 + 空数组 | EP02-S05 |
| 8 | match 精确反查 W001 → 200 + 完整记录 | EP02-S06 |
| 9 | match 模糊反查 → 200 + ≤20 候选 + similarity 排序 | EP02-S06 |
| 10 | match 未匹配 → 200 + 空候选（业务级，不阻塞） | EP02-S06 / FB1 |
| 11 | match DB 异常 → 5xx + Sentry（不伪装空候选） | FB1 |
| 12 | 软删 style 有 active sku → 409 STYLE_HAS_ACTIVE_SKU | BR-U02-21 |
| 13 | 软删 sku（无引用） → 200 | BR-U02-20 |
| 14 | 多租户隔离：A 看不到 B 的 style | EP01-S07 回归 |
| 15 | RLS：租户 ctx 缺失时 SELECT style → 0 行 | RLS 回归 |
| 16 | upsert_sku 复用同一套校验（重复调用 → 后者更新前者） | FB7 |
| 17 | 5 万 style 模糊匹配性能 → P95 ≤ 300ms | FB2 / SLA |

### 9.3 性能基准测试
- `tests/performance/test_match_perf.py`：mock 5 万 style，measure P95
- 在 CI 中可选执行（`pytest -m performance` slow tests）
- 失败阈值：P95 > 500ms 时 CI 警告（不阻断）

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| U02 NFR 全部继承 U01 基线 + 增量明确 | ✅ |
| match 降级语义严格区分业务/系统失败 | ✅ |
| 模糊搜索 SLA 与索引方案匹配（GIN trgm 必建） | ✅ |
| migration 走专用 job（与 U01 Q11=B 一致） | ✅ |
| 健康端点 /health + /ready（与 U01 实现一致） | ✅ |
| cost_price 不加密决策附威胁模型边界 | ✅ |
| Prometheus 主导 SLA 监控，Sentry 抽样异常 | ✅ |
| upsert_sku 严格复用同一套校验 | ✅ |
| 测试场景覆盖 7 条 P1 反馈 | ✅ |
