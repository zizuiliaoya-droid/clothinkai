# U02 测试覆盖

> 65+ 测试用例，覆盖 EP02-S01~S06 全部验收 + 7 条 P1 反馈对应场景。

---

## 1. 测试目录结构

```
backend/tests/
├── conftest.py                      # 修改：追加 product_factory + 角色 fixture
├── unit/
│   ├── test_field_permissions.py    # PRICE_VISIBLE_ROLES 矩阵
│   ├── test_sku_domain.py           # SKU 业务规则 + audit 脱敏
│   └── test_style_domain.py         # Style audit 仅 style_code
├── integration/
│   ├── test_style_crud.py           # EP02-S01/S03 + 软删
│   ├── test_sku_crud.py             # EP02-S02/S04/S05 + 字段权限
│   ├── test_brand_crud.py           # Brand CRUD
│   ├── test_style_match.py          # EP02-S06 + FB1 降级语义
│   └── test_sku_upsert.py           # FB7 upsert 边界
├── api/
│   └── test_product_api.py          # 鉴权 + OpenAPI 暴露
└── performance/
    ├── __init__.py
    └── test_match_perf.py           # 5 万 style P95 ≤ 300ms (@pytest.mark.performance)
```

---

## 2. 单元测试用例（30 用例）

### test_field_permissions.py（13 用例）
| 用例 | 验证 |
|---|---|
| `test_role_visibility[admin]` | admin 角色可见价格 |
| `test_role_visibility[merchandiser]` | 跟单可见价格 |
| `test_role_visibility[finance]` | 财务可见价格 |
| `test_role_visibility[pr]` | PR 不可见 |
| `test_role_visibility[pr_manager]` | PR 主管不可见（FB7） |
| `test_role_visibility[designer]` | 设计师不可见 |
| `test_role_visibility[design_assistant]` | 设计助理不可见 |
| `test_role_visibility[pattern_maker]` | 版师不可见 |
| `test_role_visibility[operations]` | 运营不可见 |
| `test_role_visibility[empty]` | 无角色不可见 |
| `test_role_visibility[multi_one_match]` | 多角色命中其一即可 |
| `test_role_visibility[multi_no_match]` | 多角色都不在白名单 |
| `test_set_input` / `test_frozenset_input` | 集合输入兼容 |
| `test_role_set_immutable` | frozenset 防误改 |
| `test_expected_roles_match_u01_seed` | 角色 code 与 U01 seed 对齐（防 follower vs merchandiser） |

### test_sku_domain.py（11 用例）
| 用例 | 验证 |
|---|---|
| `test_self_produced_requires_cost` | 自产缺 cost_price → 422 |
| `test_self_produced_with_cost_ok` | 自产有 cost_price 通过 |
| `test_external_purchase_requires_purchase_price` | 外采缺 purchase_price → 422 |
| `test_external_purchase_with_purchase_ok` | 外采有 purchase_price 通过 |
| `test_mixed_allows_all_null` | 混合允许两价格都 NULL |
| `test_negative_cost_raises` | 负数 cost_price → 422 |
| `test_zero_cost_ok` | 0 价格通过 |
| `test_sensitive_value_fields_redacted` | cost_price/purchase_price 脱敏 |
| `test_non_sensitive_excluded` | color/size 不写 audit |
| `test_constants_well_defined` | SENSITIVE_FIELDS 配置正确 |
| `test_no_changes_returns_empty` | dict diff：无变更返回空 |
| `test_cost_price_change_detected` | dict diff：检测变更 |
| `test_only_set_fields_in_diff` | dict diff：仅显式 set 字段 |

### test_style_domain.py（6 用例）
| 用例 | 验证 |
|---|---|
| `test_only_style_code_in_audit` | Style audit 仅含 style_code |
| `test_no_audit_when_only_normal_fields` | 普通字段变更不写 audit |
| `test_constants` | STYLE_SENSITIVE_FIELDS = {style_code} |
| `test_unchanged_returns_empty` | dict diff：无变更返回空 |
| `test_style_name_change_detected` | dict diff：检测变更 |
| `test_only_set_fields_in_diff` | PATCH 语义 |

---

## 3. 集成测试用例（37 用例）

### test_style_crud.py（7 用例）

| 用例 | 验收映射 |
|---|---|
| `test_create_basic` | EP02-S01.given1（创建成功） |
| `test_create_duplicate_style_code` | EP02-S01.given2（409 冲突） |
| `test_update_style_name_no_audit_for_name` | EP02-S03.given1 |
| `test_update_unchanged_returns_same` | EP02-S03.given2（BR-U02-32 未变更不写 audit） |
| `test_update_nonexistent_raises` | 404 处理 |
| `test_soft_delete_blocked_by_active_sku` | BR-U02-21 |
| `test_soft_delete_no_skus` | 无 SKU 可软删 |
| `test_list_pagination` | 分页 25/10 |

### test_sku_crud.py（11 用例）

| 用例 | 验收映射 |
|---|---|
| `test_create_basic` | EP02-S02.given1 |
| `test_create_with_invalid_style_id` | EP02-S02.given3（422 INVALID_STYLE_REFERENCE） |
| `test_create_duplicate_sku_code` | EP02-S02.given2（409） |
| `test_merchandiser_can_write_cost_price` | EP02-S04.given1 |
| `test_designer_cannot_write_cost_price` | EP02-S04.given2（403 FIELD_PERMISSION_DENIED） |
| `test_pr_cannot_see_cost_price_in_response` | 字段读权限（cost_price=null for PR） |
| `test_finance_can_see_cost_price` | 财务可见价格 |
| `test_list_returns_skus` | EP02-S05.given1（6 SKU） |
| `test_list_empty_returns_empty` | EP02-S05.given2（空数组 200） |
| `test_list_unknown_style_raises_404` | 404 处理 |
| `test_soft_delete_no_references` | BR-U02-20（U02 阶段引用 0） |

### test_brand_crud.py（5 用例）

| 用例 | 验证 |
|---|---|
| `test_create_brand` | 创建成功 |
| `test_duplicate_brand_code` | 409 BRAND_CODE_CONFLICT |
| `test_disable_brand` | 软停用 |
| `test_get_nonexistent_raises` | 404 |
| `test_update_brand_name` | 编辑 |

### test_style_match.py（9 用例）— 含 FB1 关键测试

| 用例 | 验证 |
|---|---|
| `test_match_by_code_found` | EP02-S06.given1（精确匹配） |
| `test_match_by_code_not_found_returns_empty` | EP02-S06.given3（业务未匹配 200 + 空候选） |
| `test_match_by_code_inactive_excluded` | 停用款式不命中 |
| `test_display_short_name_falls_back` | BR-U02-53（short_name=NULL → style_name） |
| `test_match_keyword_returns_candidates` | EP02-S06.given2（模糊匹配） |
| `test_match_keyword_empty_no_match` | 业务未匹配 200 + 空 |
| `test_match_keyword_blank_returns_empty` | 空白 keyword 200 + 空 |
| **`test_db_error_propagates`** | **FB1：模糊匹配系统失败让异常冒泡 → 5xx，绝不返回空候选** |
| **`test_db_error_in_exact_match_propagates`** | **FB1：精确匹配同样行为** |

### test_sku_upsert.py（5 用例）— 含 FB7 关键测试

| 用例 | 验证 |
|---|---|
| `test_first_call_inserts` | INSERT 路径 |
| `test_repeated_call_updates_same_row` | UPDATE 同一 sku.id（不创建新行） |
| `test_invalid_style_reference_raises` | **FB7：upsert 复用 style_id 校验** |
| `test_invalid_sourcing_price_raises` | **FB7：upsert 复用 sourcing 一致性校验** |
| `test_field_permission_denied_for_designer` | **FB7：upsert 复用字段权限校验**（设计师 import 也被拒） |

---

## 4. API 测试用例（6 用例）

| 用例 | 验证 |
|---|---|
| `test_styles_list_requires_auth` | 401 鉴权 |
| `test_skus_create_requires_auth` | 401 |
| `test_brands_list_requires_auth` | 401 |
| `test_match_requires_auth` | 401 |
| `test_create_style_validates_payload` | 401/422 schema 校验（中文 style_code） |
| `test_openapi_exposes_product_endpoints` | OpenAPI 暴露全部 18 端点 |

---

## 5. 性能基准测试（1 用例）

| 用例 | 验证 |
|---|---|
| `test_match_p95_under_300ms_with_50k_styles` | 5 万 style 模糊匹配 P95 ≤ 300ms |

执行节奏：
- CI PR：跳过（`@pytest.mark.performance` 标记）
- nightly schedule：跑 + 失败发 Slack/邮件
- release 前：SRE 手动跑

测试包含：
1. 生成 50K mock style + ANALYZE
2. EXPLAIN ANALYZE 验证 GIN trgm 索引命中（`Bitmap Index Scan on idx_style_search_trgm`）
3. 100 次 match 测量 P50/P95/P99
4. P95 > 300ms 时打印诊断顺序提示

---

## 6. 覆盖率统计

| 文件 | 期望覆盖率 | 实际场景 |
|---|---|---|
| `service.py` | ≥ 80% | StyleService 11 方法 / SkuService 7 方法全覆盖 |
| `repository.py` | ≥ 70% | 列表 / 搜索 / upsert / count 全覆盖 |
| `domain.py` | ≥ 90% | 全部 BR-U02-NN 业务规则 + dict diff + audit |
| `api.py` | ≥ 60% | 18 端点鉴权 + 校验 |
| `legacy_field_permissions.py` | 100% | has_price_visibility 矩阵全覆盖 |

---

## 7. 7 条 P1 反馈测试映射

| 反馈 ID | 关键测试 |
|---|---|
| **FB1** match 降级语义 | `test_db_error_propagates`（模糊）+ `test_db_error_in_exact_match_propagates`（精确）+ `test_match_by_code_not_found_returns_empty`（业务未匹配） |
| **FB2** GIN 索引 + SLA | `test_match_p95_under_300ms_with_50k_styles` 显式验证 `Bitmap Index Scan on idx_style_search_trgm` |
| **FB3** migration 通道 | 通过 `migrate.yml` 执行，与 U01 一致（不在测试中验证） |
| **FB4** 健康端点 /health + /ready | 由 U01 main.py 既有实现保证 |
| **FB5** cost_price 不加密 | 字段权限测试覆盖；威胁模型边界由设计文档表述 |
| **FB6** Prometheus 主导 SLA | `core/metrics.py` 接入 + service 层 record；端到端验证由 SRE 在 staging 执行 |
| **FB7** upsert 边界严格 | 5 用例覆盖：复用 style_id 校验 / sourcing 一致性 / 字段权限 / INSERT/UPDATE 路径 |

---

## 8. 测试运行命令

```bash
# 单元 + 集成（默认）
cd backend && pytest tests/ -v

# 仅单元
cd backend && pytest tests/unit -v

# 仅集成
cd backend && pytest tests/integration -v

# 仅 API
cd backend && pytest tests/api -v

# 性能（需要真实 PG + 较长时间）
cd backend && pytest tests/performance -v -m performance

# 覆盖率报告
cd backend && pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=70
```
