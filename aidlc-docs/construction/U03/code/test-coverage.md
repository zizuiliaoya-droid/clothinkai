## U03 测试覆盖

> 53+ 测试用例，覆盖 EP04-S01~S03 全部验收 + 防侧信道关键测试。

---

## 1. 测试目录结构

```
backend/tests/
├── conftest.py                         # 修改：追加 pr_manager_role + blogger_factory
├── unit/
│   ├── test_blogger_field_perms.py     # 字段权限矩阵
│   └── test_blogger_domain.py          # audit 脱敏 + dict diff
├── integration/
│   ├── test_blogger_crud.py            # EP04-S01/S02
│   ├── test_blogger_search.py          # EP04-S03 + 防侧信道
│   └── test_blogger_upsert.py          # FB7 upsert 边界
├── api/
│   └── test_blogger_api.py             # 鉴权 + OpenAPI
└── performance/
    └── test_blogger_search_perf.py     # 3000 博主 P95 ≤ 200ms
```

---

## 2. 单元测试用例（23 用例）

### test_blogger_field_perms.py（13 用例）
- TestQuoteVisibility（10 子用例）：admin/pr/pr_manager/finance 可见 / merchandiser/designer/operations 不可见 / 多角色组合
- TestContactVisibility（10 子用例）：admin/pr/pr_manager 可见 / **finance 不可见**（与 quote 区分）
- TestQuoteWritable（5 子用例）：admin/pr/pr_manager 可写 / finance 不可写（仅读）
- 常量值断言

### test_blogger_domain.py（10 用例）
- TestBloggerAuditChanges（3 用例）：quote/wechat/phone 脱敏 + 非敏感字段排除 + 常量定义
- TestComputeBloggerChanges（3 用例）：unchanged 返回空 / 检测变更 / PATCH 语义

---

## 3. 集成测试用例（25 用例）

### test_blogger_crud.py（8 用例）

| 用例 | 验收映射 |
|---|---|
| `test_create_basic` | EP04-S01.given1 |
| `test_create_duplicate_returns_existing_id` | EP04-S01.given2（409 + existing_blogger_id 引导） |
| `test_update_quote_with_pr` | EP04-S02 PR 编辑成功 |
| `test_update_quote_with_finance_denied` | finance 仅读不写 quote → 403 |
| `test_update_quote_with_designer_denied` | designer 不可写 quote → 403 |
| `test_update_unchanged_returns_same` | BR-U03-31 未变更不写 audit |
| `test_update_nonexistent_raises` | 404 |
| `test_soft_delete_no_references` | BR-U03-20（U03 阶段引用 0） |

### test_blogger_search.py（13 用例）

#### TestKeywordSearch（2 用例）
- `test_match_nickname` — 命中 nickname
- `test_match_xiaohongshu_id` — 命中 xiaohongshu_id

#### **TestSideChannelProtection（3 用例 — 关键防侧信道测试）**
- `test_pr_can_match_wechat` — PR 角色 keyword 命中 wechat（有权限）
- `test_designer_cannot_match_wechat` — designer 不能通过 keyword 命中 wechat（防侧信道）
- `test_finance_cannot_match_wechat` — finance 虽可读 quote 但无 CONTACT_VISIBLE，wechat 不参与匹配

#### TestFieldVisibilityInResponse（3 用例）
- `test_pr_sees_quote_and_contact` — PR 见 quote/wechat/phone
- `test_finance_sees_quote_only` — finance 见 quote 不见 wechat/phone
- `test_designer_sees_neither` — designer 都不见

#### TestRangeAndTagFilters（2 用例）
- `test_follower_count_range` — 范围筛选
- `test_category_tag_filter` — JSONB tag 包含查询

#### TestSystemFailureNotMaskedAsEmpty（1 用例）
- `test_db_error_propagates` — DB 异常冒泡，不伪装空数组

### test_blogger_upsert.py（4 用例）— FB7 边界

- `test_first_call_inserts` — INSERT 路径
- `test_repeated_call_updates_same_row` — UPDATE 同一 blogger.id
- `test_designer_field_permission_denied` — designer 不可 import quote
- `test_finance_quote_write_denied` — finance import 写 quote 也被拒（与编辑路径一致）

---

## 4. API 测试用例（4 用例）

| 用例 | 验证 |
|---|---|
| `test_list_requires_auth` | 401 鉴权 |
| `test_create_requires_auth` | 401 |
| `test_create_validates_payload` | 401/422 schema 校验 |
| `test_openapi_exposes_blogger_endpoints` | OpenAPI 暴露全部 7 端点 |

---

## 5. 性能基准测试（1 用例）

`test_search_p95_under_200ms_with_3k_records`：
1. 生成 3000 mock blogger + ANALYZE
2. EXPLAIN ANALYZE 验证 GIN 命中
3. 100 次搜索测量 P50/P95/P99
4. P95 > 200ms 时打印诊断提示

执行节奏：
- CI PR：跳过（@pytest.mark.performance）
- nightly：跑 + 失败告警
- release 前：SRE 手动跑

---

## 6. 覆盖率门槛

| 文件 | 期望覆盖率 |
|---|---|
| `service.py` | ≥ 80% |
| `repository.py` | ≥ 70% |
| `domain.py` | ≥ 90% |
| `api.py` | ≥ 60% |
| `legacy_field_permissions.py` | 100% |

---

## 7. 关键反馈测试映射

| 反馈点 | 关键测试 |
|---|---|
| **防侧信道**：wechat 在无权限时不参与 keyword 匹配 | `TestSideChannelProtection` 3 个用例（pr 命中 / designer 不命中 / finance 不命中） |
| **字段权限脱敏**：audit 不存敏感值 | `test_blogger_domain.py:TestBloggerAuditChanges` |
| **finance vs CONTACT_VISIBLE 区分**：finance 见 quote 不见 wechat | `test_finance_sees_quote_only` + `test_finance_cannot_match_wechat` |
| **upsert 复用校验** | `test_designer_field_permission_denied` + `test_finance_quote_write_denied` |
| **降级语义**：系统失败不伪装空数组 | `test_db_error_propagates` |
| **EP04-S01 引导语义**：返回 existing_blogger_id | `test_create_duplicate_returns_existing_id` |

---

## 8. 测试运行命令

```bash
# 单元 + 集成（默认）
cd backend && pytest tests/ -v

# 仅 U03 单元测试
cd backend && pytest tests/unit/test_blogger_*.py tests/integration/test_blogger_*.py -v

# 仅性能
cd backend && pytest tests/performance/test_blogger_search_perf.py -v -m performance

# 覆盖率
cd backend && pytest tests/ --cov=app/modules/blogger --cov-report=term-missing --cov-fail-under=70
```
