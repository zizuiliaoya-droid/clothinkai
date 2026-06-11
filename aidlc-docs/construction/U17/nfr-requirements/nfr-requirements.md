# U17 NFR 需求（套装 + BI 看板 + 报表导出）

> 增量式：复用 U01 NFR 基线 + U02 product NFR + U14 report SLA + openpyxl（U06a）。本文仅列 U17 特异 NFR。
> 单元：EP02-S08、EP09-S06、EP09-S08（V2 收官单元）

---

## 1. 性能

| 项 | 指标 | 说明 |
|---|---|---|
| bundle create/get | P95 ≤ 200ms | 单 bundle + N items |
| BI get_dashboard | ≤ 1s | 串行 3 report service 聚合，复用各 SLA |
| 导出 work-progress/store-daily | ≤ 2s | 单 service + openpyxl 序列化 |
| 导出 production | ≤ 3s | 跨表聚合（U14 口径）+ 序列化 |
| 布局偏好读写 | P95 ≤ 100ms | user_preference 单行 upsert/get |

- BI 不做 precompute 缓存（V2 数据量可控）。
- split_quantities 纯函数 O(items)。

---

## 2. 导出内存与流式

- openpyxl `write_only=True` Workbook（lxml 增量写）+ BytesIO，避免一次性创建全部 cell 对象。
- 报表行数 V2 量级（千级）可控；StreamingResponse 返回流。
- 导出全量无分页，但时间筛选跨度 ≤ 366 天（resolve_time_range 约束）限制数据规模。
- Decimal/None 序列化为字符串/空（避免 openpyxl 类型异常）。

---

## 3. 安全

### 3.1 威胁模型
| 威胁 | 缓解 |
|---|---|
| 跨租户读 bundle/偏好 | RLS + 显式 WHERE tenant_id |
| bundle_item 挂跨租户 sku | 创建时校验 sku 同租户（不存在/跨租户 422） |
| 他人偏好被读写 | user_preference 按 user_id 隔离（本人） |
| 越权导出 | require_permission report.export:read → 403 |
| 导出泄露跨租户数据 | report service 已 RLS + 显式 tenant，导出仅当前租户 |

### 3.2 权限边界
- product.bundle:read/write（merchandiser + admin）。
- report.export:read（pr_manager + operations + admin）——独立于 report.*:read（仅查看）。
- BI 看板 report.*:read（运营已有）。
- user_preference 本人 user_id 隔离，无额外 scope。

---

## 4. 并发与一致性

- bundle create：UNIQUE(tenant, bundle_code) + IntegrityError → 409。
- bundle_item：UNIQUE(tenant, bundle_id, sku_id) 防同 sku 重复。
- user_preference：upsert ON CONFLICT(tenant, user_id, pref_key)。
- 导出只读无并发问题；split_quantities 纯函数无副作用。

---

## 5. 多租户与迁移

- bundle_product / bundle_item / user_preference 继承 TenantScopedModel + RLS。
- 测试 bypass 角色（RLS OFF）→ 显式 WHERE tenant_id。
- migration 021：3 表（RLS + idx + CHECK + UNIQUE）+ product.bundle/report.export scope seed。down 安全 drop 3 表 + 删 scope；无回填。

---

## 6. 可观测性

| 指标 | 类型 | labels | 用途 |
|---|---|---|---|
| report_export_total | Counter | report_type, result(success/forbidden/invalid) | 导出次数/结果 |

- bundle/BI 复用 U01 metrics + 审计（bundle 创建留痕）。

---

## 7. 测试矩阵

| 层 | 文件 | 覆盖 |
|---|---|---|
| unit | test_bundle_export.py | split_quantities（A×1+B×2 卖 3 → [(A,3),(B,6)]）+ 导出行序列化（Decimal/None → str/空） |
| integration | test_bundle_bi_export.py | bundle create + item sku 跨租户校验 422 + split + user_preference upsert/get_or_default + 导出生成 xlsx 字节流 openpyxl 可解析 + RLS |
| api | test_bundle_export_api.py | /api/bundles + /api/reports/bi + /api/reports/{type}/export 401/403 + OpenAPI |

- 覆盖率门 ≥70%（全量回归）。

---

## 8. 一致性校验

- 与 functional-design business-rules BR-U17-01~63 引用一致。
- 性能/数据复用 U14 report service + U02 product，无重复实现。
- 复用 openpyxl（U06a 既有），无新依赖。
