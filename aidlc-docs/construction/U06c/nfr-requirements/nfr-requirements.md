# U06c 非功能需求（NFR Requirements）

> 单元：U06c — 博主导入适配器
> 范围：U06c 特异性 NFR 增量；框架级 NFR 继承 U06a，通用 NFR 继承 U01-U05
> 定位：**极小增量**（单实体 adapter；无新表/端点/Celery/依赖/配置/指标）

---

## 1. 与基线的关系

### 1.1 完全继承
- U06a 框架 NFR（异步吞吐 5 万行≤5min / upload P95≤2s / 解析内存 O(1) / Celery 失败语义 / 行级隔离 FB-C / NF-1 per-row SET LOCAL / 文件威胁 / CSV injection / 5 指标）
- U03 NFR（BloggerRepository.upsert_atomic 原子性 / GIN trgm+JSONB / 联系方式字段权限过渡 CONTACT_VISIBLE_ROLES，U09 前 MVP 不强制字段级）
- U01 通用 NFR

### 1.2 U06c 增量（3 项）
1. 解析正确性：list（标签 → JSONB 数组）+ int（follower_count）+ Decimal（quote，禁 float）
2. upsert 幂等：重跑 / 重复 xiaohongshu_id 不产生重复 blogger
3. 跨租户正确性：runner per-row SET LOCAL 下 blogger.tenant_id 正确

---

## 2. 性能 NFR

### 2.1 adapter 每行开销
- **恒为 1 次 DB 往返**（单次 upsert_atomic；无关联查询）→ 比 U06b 更轻，5 万行 ~150-200 行/秒，不拖累 U06a SLA
- 解析：_split_tags / int / Decimal 均纯 Python O(字段长)，无 DB

### 2.2 容量
- 复用 U06a IMPORT_MAX_ROWS=50000；5 万行 = 最多 5 万 blogger upsert；import_job ≤ 5 万/batch

---

## 3. 正确性 NFR（U06c 核心）

### 3.1 类型解析

| type | 规则 |
|---|---|
| list（category_tags/quality_tags） | 按 `;` `；` `,` `，` 拆分 + strip + 去空 → JSONB 数组；空 → `[]` |
| int（follower_count） | 去千分位 + int；非法/负数 → 行校验失败；空 → None |
| decimal（quote） | 去千分位 + `Decimal`（**禁 float**）；非法/负数 → 失败；空 → None |
| str | strip；空 → None |

### 3.2 upsert 幂等

| 场景 | 机制 |
|---|---|
| 同文件 upload | U06a hash 去重 → 409 |
| 同 batch retry only_failed | 失败行重跑；UNIQUE(batch_id,row_number) 原地更新 |
| 文件内重复 xiaohongshu_id | U03 ON CONFLICT(tenant,xiaohongshu_id) DO UPDATE（第二行 UPDATE，is_inserted=False） |

### 3.3 跨租户正确性
- runner per-row SET LOCAL（NF-1）+ ORM before_flush 注入 + upsert_atomic 显式 tenant_id
- NFR 测试：runner 创建 blogger.tenant_id == batch.tenant_id（真实 adapter）

---

## 4. 可靠性 NFR
- 行级校验/upsert 异常 → import_job.failed（per-row 隔离 + bypass 兜底，继承 U06a）
- partial batch：成功行 commit，失败行未入库，retry only_failed

---

## 5. 安全 NFR
- 复用 U06a 文件威胁模型（白名单/上限/路径隔离/openpyxl read_only）+ csv_safe（失败下载）
- raw_data 保真（不转换）
- **联系方式（wechat/phone）不回显到 structlog**（adapter 不打印 parsed；MVP 对 blogger:write 角色可见，U09 后切字段级 CONTACT_VISIBLE_ROLES）

---

## 6. 可观测性 NFR
- 复用 U06a 5 指标（label source=manual_blogger）；无新增；adapter 不逐行打点

---

## 7. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | parse_row（_split_tags 多分隔符/空 + int 千分位/负数 + Decimal/各 str）+ validate（必填 2 项 + 数值非负 + 长度） |
| 集成 | 注册真实 adapter → upload 样本 CSV → _run_import_batch → blogger 入库 + category_tags JSONB 数组 + follower int + quote Decimal + 同 ID UPDATE 幂等 + partial + tenant_id 正确 |
| 覆盖率 | adapter ≥ 85% |
| 异步 | 直接 await _run_import_batch（monkeypatch session + mock get_object_bytes，仿 U06a/U06b） |

---

## 8. 故事 NFR 映射

| 故事 | U06c 特有 NFR |
|---|---|
| EP07-S07 上传 | source=manual_blogger 端到端入库；每行 1 DB 往返 |
| EP07-S08 去重 | 复用 U06a hash |
| EP07-S09 映射 | manual_blogger mapping=None 回退 + 自定义覆盖 |
| EP07-S10 下载/重试 | 缺 xiaohongshu_id → failed → retry only_failed 幂等 |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 每行 1 DB 往返 | ✅ §2.1 |
| list/int/Decimal 解析（禁 float） | ✅ §3.1 |
| upsert 幂等 3 场景 | ✅ §3.2 |
| 跨租户 tenant_id | ✅ §3.3 + §7 |
| 联系方式日志不回显 | ✅ §5 |
| 复用 5 指标无新增 | ✅ §6 |
| 无新依赖/服务/配置 | ✅ §1.1 |
