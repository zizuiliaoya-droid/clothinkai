# U03 NFR 设计计划（NFR Design Plan）

> 单元：U03 — 博主库基础  
> 范围：U03 特异性 NFR 设计模式 + 逻辑组件；通用模式继承 U01 + U02

---

## 1. 单元上下文

### 1.1 与 U01 + U02 NFR Design 的关系

U01 已落地 9 个通用模式（多租户 / 审计 / 状态机 / 附件 / 速率限制 / 错误处理 / 监控 / 备份 / 健康检查）。
U02 已落地 4 个增量模式（GIN trgm + 降级语义 / 字段权限硬编码过渡 / 数据库原子 upsert / 软删引用检查）。

**U03 直接复用 U02 全部 4 个模式**，仅做以下适配：
- 字段对象从 cost_price/purchase_price → quote/wechat/phone
- 索引方案从拼接表达式 GIN → 单字段 GIN trgm + GIN JSONB
- 搜索增加防侧信道（wechat 在无权限时不参与匹配）

### 1.2 输入文档
- U03 functional-design 3 文档
- U03 nfr-requirements 2 文档
- U02 nfr-design 2 文档（参考 Pattern P-U02-01~04）

### 1.3 输出文档
- `U03/nfr-design/nfr-design-patterns.md`（4 个适配模式 + 监控 SLO）
- `U03/nfr-design/logical-components.md`（U03 新增组件 + 复用清单）

---

## 2. 计划步骤

### Step 1 — 分析 NFR 需求
- [x] 1.1 读取 NFR Requirements 2 份文档
- [x] 1.2 与 U02 Pattern P-U02-01~04 对齐适配点

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U03 增量适配点
- [x] 2.2 列出澄清问题（已预填默认值）

### Step 3 — 生成 nfr-design-patterns.md
- [x] 3.1 P-U03-01：单字段 GIN trgm 模糊搜索 + 防侧信道
- [x] 3.2 P-U03-02：JSONB tag GIN 索引（U02 没有，U03 新增）
- [x] 3.3 复用 U02 Pattern 清单（字段权限 / upsert / 软删引用）
- [x] 3.4 监控点 / SLO 实施

### Step 4 — 生成 logical-components.md
- [x] 4.1 U03 新增组件清单
- [x] 4.2 组件依赖图
- [x] 4.3 复用 U01/U02 组件的具体方式

### Step 5 — 提交完成消息 + 等待审批

---

## 3. 澄清问题（请填 [Answer]）

> 因 U03 高度复用 U02 模式，仅 3 个核心问题需要确认。

### 3.1 防侧信道实现细节

**Q1**：`/api/bloggers/?keyword=` 中 wechat 字段在无 CONTACT_VISIBLE_ROLES 时是否参与匹配？

[Answer]: **不参与匹配** — service 层在构造 query 前先检查角色：
```python
clauses = [Blogger.nickname.ilike(pattern), Blogger.xiaohongshu_id.ilike(pattern)]
if has_contact_visibility(role_codes):
    clauses.append(Blogger.wechat.ilike(pattern))
stmt = stmt.where(or_(*clauses))
```
理由：避免无 wechat 读权限的角色通过 keyword 搜索结果命中（即使响应中 wechat=null，命中行为本身仍泄露信息）。

### 3.2 性能基准测试

**Q2**：U03 是否需要 5 万行性能基准测试（与 U02 同模式）？

[Answer]: 不需要。U03 MVP 上限 3000 博主，nightly 性能测试目标改为：
- `test_blogger_search_perf_with_3k_records`：3000 博主 + 100 次搜索 P95 ≤ 200ms
- 命中 GIN trgm 索引（EXPLAIN ANALYZE 必须显示 `Bitmap Index Scan`）
- `@pytest.mark.performance` 标记，PR 不阻塞

### 3.3 BloggerService 与 U10b 的扩展点

**Q3**：U10b BloggerTagService 的扩展契约？

[Answer]: U03 阶段 BloggerService 提供 4 个钩子方法占位（空实现，TODO U10b）：
- `recompute_blogger_type(blogger_id)` — 按 follower_count 自动计算
- `recompute_quality_tags(blogger_id)` — 自动追加质量标签
- `mark_suspected_fake(blogger_id, reason)` — 假号自动判定
- `bulk_recompute_tags()` — 定时批量任务（U10b Celery beat 调度）

U03 阶段方法体仅 `# TODO U10b: implement` + 抛 `NotImplementedError`，不在 U03 测试覆盖。

---

## 4. 决策摘要（用户填答后由 AI 整理）

> 用户回复"继续"后，AI 总结 [Answer] 形成最终设计清单。
