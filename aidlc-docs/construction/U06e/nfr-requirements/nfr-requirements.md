# U06e 非功能需求（NFR Requirements）

> 单元：U06e — 结算导入适配器（历史迁移）
> 范围：U06e 特异性 NFR 增量；框架级继承 U06a，通用继承 U01-U05
> 定位：小增量（历史迁移 INSERT-only + promotion 派生 + 不触发事件 + UNIQUE 一对一）

---

## 1. 与基线的关系

### 1.1 完全继承
- U06a 框架 NFR（异步吞吐 / upload P95 / 解析内存 / Celery 失败语义 / 行级隔离 FB-C / NF-1 per-row SET LOCAL / 文件威胁 / CSV injection / 5 指标）
- U05 NFR（next_settlement_sequence FB2 原子 / settlement GIN trgm / FB3 永久不可替换 / 金额字段权限 PAYMENT_VISIBLE_ROLES）
- U04 NFR（promotion 查询 + RLS）
- U01 通用 NFR

### 1.2 U06e 增量（4 项）
1. 解析正确性：Decimal（amount/total/payment 禁 float）+ date（settlement/payment）+ status 枚举
2. promotion FK 派生正确性：blogger/style/pr 从 promotion 派生
3. UNIQUE(promotion_id) 一对一幂等：重复 promotion → 行失败（FB3 不覆盖）
4. 不触发事件 + 不经 Service：导入无副作用事件

---

## 2. 性能 NFR

### 2.1 adapter 每行开销

| 操作 | DB 往返 |
|---|---|
| promotion 查（get_by_internal_code） | 1 |
| next_settlement_sequence（INSERT ON CONFLICT RETURNING） | 1 |
| settlement INSERT flush | 1 |
| **合计** | **3 次/行** |

- 比 U06d（4-5）少（promotion 派生 blogger/style，省去独立查）
- SLA：5 万行历史迁移 ≤ 6-8 分钟（一次性运维操作，宽松）

### 2.2 容量
- 复用 IMPORT_MAX_ROWS=50000
- **settlement_sequence 当天上限 9999**（U05 CHECK）：单 settlement_date 单 batch >9999 → 序列溢出失败（记入文档；历史迁移按多日期分布不触发）

---

## 3. 正确性 NFR（U06e 核心）

### 3.1 类型解析

| type | 规则 |
|---|---|
| decimal（amount/total/payment） | 去千分位 + Decimal（**禁 float**）；非法/负数 → 行失败；amount/total 必填空则失败 |
| date（settlement/payment） | `date.fromisoformat`；非法 → 行失败；settlement_date 必填空则失败 |
| status | ∈ {待核查,待付款,待财务付款,已付款,已驳回}；空 → 默认待核查 |

### 3.2 promotion FK 派生
- promotion_internal_code → promotion（get_by_internal_code）；未找到 → 行失败
- blogger_id/style_id/pr_id **从 promotion 派生**（保证与 promotion 一致，不让文件提供）
- promotion 查询受 RLS（仅本租户）

### 3.3 UNIQUE(promotion_id) 一对一幂等

| 场景 | 机制 |
|---|---|
| 同文件 upload | U06a hash 409 |
| 同 batch 重复 promotion 行 | UNIQUE(batch_id,row_number)（不同行）+ UNIQUE(promotion_id)（第二行 INSERT 失败） |
| **跨文件相同 promotion** | **UNIQUE(promotion_id) DB 约束拦截 → 行失败**（区别 U06d 无此约束，U06e 幂等性更强） |
| promotion 已有事件创建的 settlement | UNIQUE(promotion_id) → 行失败（FB3 不覆盖） |

> IntegrityError catch 转 RowValidationError（per-row 事务隔离，不影响其他行）。

### 3.4 不触发事件
- adapter 不调 event_bus.dispatch；不经 U05 SettlementService
- NFR 测试：导入后不产生 SettlementPaid 等事件（event_capture 断言空）

---

## 4. 可靠性 NFR
- 行级失败（校验 / promotion 缺失 / UNIQUE 冲突 / 序列溢出）→ import_job.failed（per-row 隔离 + bypass 兜底）
- partial batch：成功行 commit，失败行未入库

---

## 5. 安全 NFR
- 复用 U06a 文件威胁 + csv_safe；raw_data 保真
- **金额字段（amount/total_amount/payment_amount）不回显 structlog**（U05 PAYMENT_VISIBLE_ROLES，MVP 对 importer.batch:write 可见，U09 切字段级）

---

## 6. 可观测性 NFR
- 复用 U06a 5 指标（label source=manual_settlement）；无新增

---

## 7. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | parse_row（_to_date/_to_decimal/默认/自定义）+ validate（必填/数值/date/status 枚举各分支） |
| 集成 | seed promotion + 已有 settlement → upload 样本 CSV → _run_import_batch → settlement 入库 + settlement_no 生成 + 派生 blogger/style + 重复 promotion failed + 缺 promotion failed + 不触发事件 + partial + tenant_id |
| 覆盖率 | adapter ≥ 85% |
| 异步 | 直接 await _run_import_batch（monkeypatch session + mock get_object_bytes + committed 清理含 settlement/settlement_sequence/seed promotion） |

---

## 8. 故事 NFR 映射

| 故事 | U06e 特有 NFR |
|---|---|
| EP07-S07 上传 | source=manual_settlement 历史迁移建 settlement；每行 3 DB 往返 |
| EP07-S08 去重 | 复用 U06a hash + UNIQUE(promotion_id) DB 强约束 |
| EP07-S09 映射 | manual_settlement mapping=None 回退 + 自定义 |
| EP07-S10 下载/重试 | promotion 缺失/UNIQUE 冲突 → failed → retry only_failed |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 每行 3 DB 往返 | ✅ §2.1 |
| Decimal/date/status 解析（禁 float） | ✅ §3.1 |
| promotion 派生（不让文件提供） | ✅ §3.2 |
| UNIQUE(promotion_id) 一对一幂等（FB3） | ✅ §3.3 |
| 不触发事件 + 不经 Service | ✅ §3.4 + §7 |
| 金额日志不回显 | ✅ §5 |
| 复用 5 指标无新增 | ✅ §6 |
