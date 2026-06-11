# U06d 非功能需求（NFR Requirements）

> 单元：U06d — 推广导入适配器
> 范围：U06d 特异性 NFR 增量；框架级继承 U06a，通用继承 U01-U05
> 定位：小增量（INSERT-only + FK 解析 + 序列生成；比 U06b/c 多）

---

## 1. 与基线的关系

### 1.1 完全继承
- U06a 框架 NFR（异步吞吐 / upload P95 / 解析内存 / Celery 失败语义 / 行级隔离 FB-C / NF-1 per-row SET LOCAL / 文件威胁 / CSV injection / 5 指标）
- U04 NFR（next_internal_sequence FB2 原子 / promotion GIN trgm / 金额字段权限过渡 AMOUNT_VISIBLE_ROLES）
- U02/U03 NFR（style/blogger 查询 + RLS）
- U01 通用 NFR

### 1.2 U06d 增量（4 项）
1. 解析正确性：Decimal（quote/cost，禁 float）+ date（cooperation/scheduled）
2. FK 解析正确性：style/blogger 必需 + sku 可选；缺失 → 行失败
3. 序列生成 + 并发：每行 next_internal_sequence（FB2 原子）；跳号可接受
4. INSERT-only 幂等限制：跨文件重复无 dedup（已知限制，文档化）

---

## 2. 性能 NFR

### 2.1 adapter 每行开销

| 操作 | DB 往返 |
|---|---|
| style 查（get_by_code，必需） | 1 |
| blogger 查（get_by_xiaohongshu_id，必需） | 1 |
| sku 查（仅 sku_code 非空） | 0 或 1 |
| next_internal_sequence（INSERT ON CONFLICT RETURNING） | 1 |
| promotion INSERT flush | 1 |
| **合计** | **4-5 次/行** |

- 比 U06b（2-3）/U06c（1）多；导入非高频
- SLA：5 万行 ~100-150 行/秒 → ≤ 6-8 分钟（**略放宽于 U06a 5 分钟基线**，记入文档；异步任务不阻塞用户）
- V1 评估：style/blogger 批量预解析缓存（按 distinct style_code/xhs_id 预查），降到 ~2 往返/行

### 2.2 容量
- 5 万行 = 5 万 promotion INSERT + 序列 UPDATE
- **promotion_sequence 当天上限 9999**（U04 CHECK）：单 cooperation_date 单 batch 超 9999 行 → 序列溢出失败（记入文档；实际按多日期分布不触发）

---

## 3. 正确性 NFR（U06d 核心）

### 3.1 类型解析

| type | 规则 |
|---|---|
| decimal（quote/cost） | 去千分位 + Decimal（**禁 float**）；非法/负数 → 行失败；空 → None（quote 必填空则失败） |
| date（cooperation/scheduled） | `date.fromisoformat`（YYYY-MM-DD）；非法 → 行失败；cooperation 必填空则失败 |
| str | strip；空 → None |

### 3.2 FK 解析

| FK | 必需性 | 缺失行为 |
|---|---|---|
| style_code → style_id | 必需 | 行失败 `款式编码 X 不存在` |
| xiaohongshu_id → blogger_id | 必需 | 行失败 `博主 X 不存在` |
| sku_code → sku_id | 可选（提供则必须有效） | 提供但无效 → 行失败；不提供 → None |

- FK 查询受 RLS 约束（per-row SET LOCAL）→ 跨租户引用自动失败（仅本租户 style/blogger 可见）
- 不建残缺 promotion（FK 缺失整行失败，per-row 事务回滚）

### 3.3 序列生成 + 并发
- next_internal_sequence（U04 FB2 单条 INSERT ON CONFLICT DO UPDATE RETURNING，原子）
- 导入单 batch 串行（U06a runner）→ 同 cooperation_date 序号连续
- 行失败回滚 → 序号 UPDATE 同 per-row 事务回滚（不浪费）；不同 batch 并发 / 行间回滚 → 可能跳号（可接受，internal_code 唯一性靠 uq_promotion_internal_code partial UNIQUE）

---

## 4. 可靠性 NFR
- 行级失败（校验 / FK 缺失 / 序列溢出 / INSERT 异常）→ import_job.failed（per-row 隔离 + bypass 兜底）
- partial batch：成功行 commit，失败行未入库
- retry only_failed：补齐 style/blogger 需在主数据侧修复后重新 upload（原 batch retry 仅重跑同 raw_data）

---

## 5. 安全 NFR
- 复用 U06a 文件威胁模型 + csv_safe
- raw_data 保真
- **金额字段（quote_amount/cost_snapshot）不回显 structlog**（U04 AMOUNT_VISIBLE_ROLES，MVP 对 promotion:write 可见，U09 切字段级）

---

## 6. 可观测性 NFR
- 复用 U06a 5 指标（label source=manual_promotion）；无新增；adapter 不逐行打点

---

## 7. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | parse_row（_to_decimal/_to_date/str + 默认/自定义 mapping）+ validate（必填/数值/date 各分支） |
| 集成 | seed style+blogger → upload 样本 CSV → _run_import_batch → promotion 入库 + internal_code 生成 + 序号连续 + 缺 style/blogger failed + sku 可选 + partial + tenant_id |
| 覆盖率 | adapter ≥ 85% |
| 异步 | 直接 await _run_import_batch（monkeypatch session + mock get_object_bytes + committed 清理含 promotion/promotion_sequence/seed） |

---

## 8. 故事 NFR 映射

| 故事 | U06d 特有 NFR |
|---|---|
| EP07-S07 上传 | source=manual_promotion 端到端建 promotion；每行 4-5 DB 往返 |
| EP07-S08 去重 | 复用 U06a hash（INSERT-only 跨文件不去重，已知限制） |
| EP07-S09 映射 | manual_promotion mapping=None 回退 + 自定义 |
| EP07-S10 下载/重试 | FK 缺失 → failed → retry only_failed |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 每行 4-5 DB 往返 + SLA 略放宽 | ✅ §2.1 |
| Decimal/date 解析（禁 float） | ✅ §3.1 |
| FK 解析必需性 + RLS 跨租户 | ✅ §3.2 |
| 序列原子 + 跳号可接受 | ✅ §3.3 |
| INSERT-only 幂等限制文档化 | ✅ §4 + §8 |
| 金额日志不回显 | ✅ §5 |
| 复用 5 指标无新增 | ✅ §6 |
