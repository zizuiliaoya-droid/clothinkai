# U03 非功能需求（NFR Requirements）

> 单元：U03 — 博主库基础  
> 范围：U03 特异性 NFR 增量；通用 NFR 全部继承 `U01/nfr-requirements/` + `U02/nfr-requirements/`

---

## 1. 与 U01/U02 NFR 基线的关系

### 1.1 完全继承（不重新定义）

| 维度 | 来源 | U03 复用方式 |
|---|---|---|
| 错误码体系 `{code, message, details}` | U01 | 沿用，新增 6 个 blogger 错误码 |
| 认证（JWT + password_changed_at + 黑名单） | U01 | 全部继承 |
| 授权（RBAC + permission 装饰器） | U01 | 沿用 + 新增 `blogger:*` 权限 |
| 多租户（ORM 注入 + RLS） | U01 | 沿用，Blogger 继承 TenantScopedModel |
| 审计（@audit + AuditService） | U01 | 沿用，新增 4 个 action 名 |
| 速率限制（slowapi 4 层） | U01 | U03 全 API 默认应用 |
| 监控（Prometheus + Sentry + Loki） | U01 | 沿用 + 新增 module=blogger tag |
| 健康检查 `/health` + `/ready` | U01 | 不新增 |
| 备份（daily/monthly tar.gz） | U01 | Blogger 表自动纳入 |
| 字段权限硬编码过渡 | **U02** | 复用模式：`legacy_field_permissions.py` + TODO U09 |
| 审计敏感值脱敏 | **U02** | 复用 `*_changed: true` 标记策略 |
| 数据库原子 upsert | **U02** | 复用 `pg_insert.on_conflict_do_update` + partial UNIQUE |
| 软删 + 引用检查 | **U02** | 复用 `check_references()` + TODO U04 |
| match 降级语义 | **U02** | 复用业务未匹配 vs 系统失败区分 |

### 1.2 U03 增量

| 维度 | 增量 | 章节 |
|---|---|---|
| 容量 | 单租户 ≤ 3000 博主（U02 是 5 万） | §2 |
| 性能 SLA | search/list/写 P95（与 U02 同等） | §3 |
| 索引 | GIN trgm 单字段 + GIN JSONB（U02 是拼接表达式 GIN） | §3.4 |
| 字段权限对象 | quote / wechat / phone（U02 是 cost_price / purchase_price） | §4 |
| 监控指标 | `blogger_search_results_count`（1 个） | §5 |
| 数据迁移 | 1763+ 历史数据由 U06c 导入，U03 不实施 | §7 |

---

## 2. 容量需求

### 2.1 数据规模（单租户）

| 表 | MVP 上限 | V1 上限 | V2+ 上限 |
|---|---|---|---|
| `blogger` | 3,000 行 | 10,000 行 | 50,000 行 |

业务文档基线 1763+ × 1.5 倍冗余 = 3000；远小于 U02 style/sku 表规模。

### 2.2 并发负载

| API | 平均 QPS | 峰值 QPS | 触发场景 |
|---|---|---|---|
| `GET /api/bloggers/` 列表 | 5 | 30 | PR / PR 主管浏览 |
| `GET /api/bloggers/?keyword=` | 10 | 50 | PR 集中筛选时段 |
| `GET /api/bloggers/{id}` 详情 | 5 | 20 | 详情页打开 |
| `POST /api/bloggers/` | 1 | 5 | PR 录入 |
| `PUT /api/bloggers/{id}` | 1 | 5 | PR 编辑报价 |

### 2.3 增长触发器
- 单租户 `blogger` 突破 3000 → P95 监控连续 1 周 > 500ms 触发：
  1. 检查 GIN 索引使用率
  2. 评估升级到拼接表达式 GIN trgm（与 U02 style 模式对齐）
- 突破 1 万 → V1+ 评估按 platform 分库（暂未列入计划）

---

## 3. 性能需求

### 3.1 SLA 总表

| API | P50 | P95 | P99 | 超时 |
|---|---|---|---|---|
| `GET /api/bloggers/` 列表（默认页 20） | ≤ 30ms | ≤ 200ms | ≤ 500ms | 3s |
| `GET /api/bloggers/?keyword=` | ≤ 50ms | ≤ 150ms | ≤ 300ms | 2s |
| `GET /api/bloggers/?category_tag=` | ≤ 30ms | ≤ 100ms | ≤ 200ms | 2s |
| `GET /api/bloggers/{id}` 详情 | ≤ 20ms | ≤ 80ms | ≤ 200ms | 2s |
| `POST /api/bloggers/` | ≤ 50ms | ≤ 150ms | ≤ 400ms | 5s |
| `PUT /api/bloggers/{id}` | ≤ 50ms | ≤ 150ms | ≤ 400ms | 5s |
| `DELETE /api/bloggers/{id}` | ≤ 50ms | ≤ 150ms | ≤ 400ms | 5s |

### 3.2 SLA 适用条件
- 测试基准：3000 博主 + 各角色组合
- 单租户独立测试

### 3.3 监控数据源
- **Prometheus** = SLA 真实数据源（与 U02 一致）
- **Sentry** = 异常 + 慢事务抽样

### 3.4 索引必建项

```sql
-- 业务键唯一 + 软删释放
CREATE UNIQUE INDEX uq_blogger_xiaohongshu_id ON blogger (tenant_id, xiaohongshu_id)
  WHERE is_deleted = false;

-- 列表 / 筛选
CREATE INDEX idx_blogger_tenant_active ON blogger (tenant_id, is_active, is_deleted);
CREATE INDEX idx_blogger_type ON blogger (tenant_id, blogger_type);
CREATE INDEX idx_blogger_follower_count ON blogger (tenant_id, follower_count);
CREATE INDEX idx_blogger_platform ON blogger (tenant_id, platform);
CREATE INDEX idx_blogger_suspected_fake ON blogger (tenant_id)
  WHERE is_suspected_fake = true;

-- GIN trgm 单字段（U03 数据量小，无需拼接表达式）
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_blogger_nickname_trgm ON blogger
  USING gin (nickname gin_trgm_ops) WHERE is_deleted = false;
CREATE INDEX idx_blogger_xhs_id_trgm ON blogger
  USING gin (xiaohongshu_id gin_trgm_ops) WHERE is_deleted = false;

-- GIN JSONB（tag 包含查询）
CREATE INDEX idx_blogger_category_tags ON blogger USING gin (category_tags);
CREATE INDEX idx_blogger_quality_tags ON blogger USING gin (quality_tags);
```

---

## 4. 安全需求

### 4.1 字段级权限（U02 模式延续 / U09 落细）

#### 4.1.1 角色硬编码可见性矩阵

| 角色 | quote | wechat | phone |
|---|---|---|---|
| admin | ✅ | ✅ | ✅ |
| pr | ✅ | ✅ | ✅ |
| pr_manager | ✅ | ✅ | ✅ |
| finance | ✅ | ❌ | ❌ |
| 其他（merchandiser/designer/operations 等） | ❌ | ❌ | ❌ |

#### 4.1.2 实施位置
`modules/blogger/legacy_field_permissions.py`：
- `QUOTE_VISIBLE_ROLES = frozenset({"admin", "pr", "pr_manager", "finance"})`
- `CONTACT_VISIBLE_ROLES = frozenset({"admin", "pr", "pr_manager"})`

带 `# TODO U09` 注释，与 U02 PRICE_VISIBLE_ROLES 同一清理模式。

#### 4.1.3 写权限
- 仅 admin / pr / pr_manager 可写 quote / wechat / phone
- finance 仅可读 quote 不可写
- 其他角色 PUT 含敏感字段 → `403 FIELD_PERMISSION_DENIED`

### 4.2 敏感字段不加密（威胁模型）

#### 4.2.1 决策
quote / wechat / phone 在数据库存明文。

#### 4.2.2 威胁模型边界
- **本决策仅防御**：普通业务用户跨角色越权读取（设计师 / 跟单 / 运营 不应看到 quote/wechat/phone）
- **本决策不防御**：DBA / 运维 / 拥有数据库直接读权限的人查看明文（视为可信内部人员）
- **应用层防护**：
  - `clothing_app` 角色（业务连接）通过 service 层 BR-U03-41 + Pydantic schema 字段过滤
  - DBA 用 `clothing_bypass`（应急专用）
- **审计**：所有 quote / wechat / phone 变更全部进 audit_log，但 audit_log 中**仅记 `*_changed: true` 标记**，不存历史值

#### 4.2.3 演进选项
若 V2+ 出现客户合规要求 → 引入 pgcrypto 字段级加密 + KMS 集成（独立单元承担）。

### 4.3 输入验证

| 字段 | 长度限制 | 字符集 | 校验位置 |
|---|---|---|---|
| `xiaohongshu_id` | ≤ 64 | 字母 + 数字 + `-` + `_` | Pydantic + DB |
| `nickname` | ≤ 128 | 任意（含中文） | Pydantic |
| `wechat` | ≤ 64 | 任意 | Pydantic |
| `phone` | ≤ 32 | 任意（不强校验中国格式，兼容历史导入） | Pydantic |
| `quote` | DECIMAL(10,2) ≥ 0 | 数字 | Pydantic + DB CHECK |
| `follower_count` | INTEGER ≥ 0 | 数字 | Pydantic + DB CHECK |
| `category_tags` / `quality_tags` | 数组 ≤ 20 项，每项 ≤ 32 | 字符串 | Pydantic |

### 4.4 搜索侧信道防护

`GET /api/bloggers/?keyword=xxx` 关键字搜索：
- 默认匹配 `nickname` / `xiaohongshu_id`
- **仅当用户具有 CONTACT_VISIBLE_ROLES 时**，才将 `wechat` 加入匹配
- 防止无 wechat 读权限的角色通过 keyword 搜索侧信道泄露 wechat

### 4.5 速率限制（继承 U01）
| 维度 | 阈值 |
|---|---|
| IP | 60 req/min |
| 用户 | 600 req/min |
| 写操作 | 30 req/min/用户 |

---

## 5. 监控与可观测性

### 5.1 Prometheus 指标

通用指标自动覆盖（`http_request_duration_seconds` 等）。

新增自定义指标：
```python
# core/metrics.py（追加）
blogger_search_results_count: Histogram = Histogram(
    "blogger_search_results_count",
    "Distribution of blogger search result counts",
    buckets=(0, 1, 5, 20, 100),
)
```

用途：监控零候选率（业务级告警）。

### 5.2 Sentry
- transaction tag：新增 `module=blogger`
- 复用 `clothing-erp-backend` 项目

### 5.3 日志（与 U02 §5.3 一致策略）
- 关键字段：`tenant_id`, `actor_id`, `request_id`, `module=blogger`, `action=blogger.create|update|...`
- **不记录敏感字段值**（quote / wechat / phone）—— audit_log 仅写 `*_changed: true` 标记

### 5.4 告警阈值

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| `histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/bloggers.*"}) > 1` 持续 5min | Prometheus alertmanager → 企微 | SRE |
| 零候选率 > 30% 持续 30min | Prometheus alertmanager | 业务 + 后端 |
| `/api/bloggers.*` 5xx > 5% 持续 5min | Sentry → 企微 | 后端 |

---

## 6. 演化兼容性

### 6.1 字段权限演化（U09）
所有 `# TODO U09` 标记位置 → grep `legacy_field_permissions` → 替换为 `Permission.field_filter()` / `field_writable()`。

### 6.2 智能博主标签（U10b）
- `blogger_type` 字段不变，赋值方式从手填改为按 `follower_count` 自动计算
- `quality_tags` JSONB 数组不变，BloggerTagService 自动追加（如 "高互动"、"真实粉丝"）
- `is_suspected_fake` 字段不变，自动判定（粉丝突增 / 互动率异常等启发式）

### 6.3 报价快照（U04）
- U03 仅保证 `Blogger.quote` 字段稳定可读
- U04 在 `promotion` 表创建时**应用层快照**：
  ```python
  promotion.quote_snapshot = blogger.quote
  ```
- 历史报价变更不影响已签合作单

### 6.4 跨平台（V1+）
- `platform` 字段已预留 4 个枚举（小红书 / 抖音 / 快手 / B站）
- V1 视实际需要扩展抖音/B站具体业务字段

---

## 7. 数据迁移

### 7.1 1763+ 博主历史数据
- **不在 U03 阶段实施**
- MVP 启用后由 U06c（博主导入适配器）通过 Excel 模板批量上传
- 调用 `BloggerService.upsert_by_xiaohongshu_id()` 内部 API（不暴露 HTTP）
- U03 提供 0 数据起步能力

### 7.2 Alembic Migration 执行

通过 `migrate.yml` 专用 job（与 U01 Q11=B + U02 一致），先 staging 后 production。

U03 单次 migration 内容：
- 创建 1 张表（blogger）
- 创建 10 个索引（含 GIN trgm + GIN JSONB）
- 启用 RLS 策略
- 追加 blogger 权限 seed（如 U01 未涵盖）

---

## 8. 可恢复性

继承 U01 备份框架：
- daily/monthly tar.gz to R2
- Blogger 表自动纳入
- 恢复演练通过 `restore_backup.py`

---

## 9. 测试覆盖需求

### 9.1 覆盖率门槛

| 文件 | 最低覆盖率 |
|---|---|
| `service.py` | ≥ 80% |
| `repository.py` | ≥ 70% |
| `domain.py` | ≥ 90% |
| `api.py` | ≥ 60% |
| `legacy_field_permissions.py` | 100% |

### 9.2 必须覆盖的集成测试场景

| # | 场景 | 验收映射 |
|---|---|---|
| 1 | 创建博主 + xiaohongshu_id 重复 → 409 + existing_blogger_id | EP04-S01 |
| 2 | 编辑 quote + admin/pr 角色 → 200 + audit `quote_changed: true` | EP04-S02 |
| 3 | 编辑 quote + designer 角色 → 403 FIELD_PERMISSION_DENIED | EP04-S02 |
| 4 | 多筛选搜索（关键字 + follower_count_min + category_tag） | EP04-S03 |
| 5 | designer 角色 GET 详情 → quote/wechat/phone 字段为 null | BR-U03-41 |
| 6 | finance 角色 GET 详情 → 见 quote 不见 wechat/phone | BR-U03-41 |
| 7 | keyword 搜索时 designer 不通过 wechat 字段命中 | BR-U03-50 防侧信道 |
| 8 | JSONB tag 包含查询命中 GIN 索引（EXPLAIN ANALYZE） | BR-U03-52 |
| 9 | upsert INSERT 路径 + audit action=blogger.create_via_import | upsert 边界 |
| 10 | upsert UPDATE 路径 + 复用 designer 角色字段权限校验 | upsert 边界 |
| 11 | 软删 + 无引用允许（U03 始终允许） | BR-U03-20 |
| 12 | 多租户隔离：A 看不到 B 的 blogger | EP01-S07 回归 |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 全部继承 U01 + U02 NFR 基线，仅增量明确 | ✅ |
| 字段权限模式与 U02 一致 | ✅ |
| 审计敏感值脱敏与 U02 一致 | ✅ |
| 监控双源（Prometheus 主 + Sentry 抽样） | ✅ |
| 搜索防侧信道（wechat 不参与无权限角色匹配） | ✅ |
| migration 通过专用 job | ✅ |
| 测试覆盖含 EP04-S01~S03 全部验收 + 字段权限矩阵 | ✅ |
