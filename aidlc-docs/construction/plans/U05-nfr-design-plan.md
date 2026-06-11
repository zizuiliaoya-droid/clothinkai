# U05 NFR 设计计划（NFR Design Plan）

> 单元：U05 — 财务结款核心  
> 范围：U05 特异性 NFR 设计模式 + 逻辑组件；通用模式继承 U01 + U02 + U03 + U04

---

## 1. 单元上下文

### 1.1 与 U01-U04 NFR Design 的关系

完全继承 U01-U04 的全部模式：
- U01 9 个通用模式（多租户 / 审计 / 状态机基类 / 附件 / 速率限制 / 错误处理 / 监控 / 备份 / 健康检查）
- U02 4 个模式（GIN trgm / 字段权限硬编码 / 数据库原子 upsert / 软删引用检查 — **U05 不复用 partial UNIQUE，FB3 改永久**）
- U03 1 个模式（GIN JSONB — U05 不需要）
- **U04 4 个模式全部复用**（FB1 状态语义 / FB2 序列号原子 / FB7 状态机 WHERE / FB8 日期一致）
- **U04 8 P1 反馈守护全部继承**（U05 不需要重新评估）

### 1.2 U05 增量（4 个新模式）

| 模式 | 解决问题 | 章节 |
|---|---|---|
| **P-U05-01** 财务记录永久不可替换 | UNIQUE 约束 + DELETE 接口 405 + 不级联软删（FB3） | §2 |
| **P-U05-02** Attachment 6 项强校验封装 | ProofAttachmentValidator helper + 跨租户监控（FB4） | §3 |
| **P-U05-03** 双口径汇总（activity + as_of） | 独立 endpoint + SQL 内嵌 + V1 Materialized View 触发条件（FB7） | §4 |
| **P-U05-04** 反向通知事件 + 部署一致性扩展 | SettlementPaid 通知类 + register_event_listeners 双向注册（FB5 + 继承 U04 FB10）| §5 |

### 1.3 输入文档
- U05 functional-design 3 文档
- U05 nfr-requirements 2 文档
- U04 nfr-design 2 文档（4 个模式 + 19 组件清单 — 复用核心契约）

### 1.4 输出文档
- `U05/nfr-design/nfr-design-patterns.md`（4 个增量模式）
- `U05/nfr-design/logical-components.md`（U05 新增组件 + 与 U04 反向 listener 双向注册）

---

## 2. 计划步骤

### Step 1 — 分析 NFR 需求
- [x] 1.1 读取 NFR Requirements 2 份文档
- [x] 1.2 与 U04 模式对齐复用边界（直接全部继承，无需重新评估 8 P1）

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U05 增量模式（4 个）
- [x] 2.2 列出澄清问题（已预填默认值）
- [x] 2.3 等待用户填答 [Answer]

### Step 3 — 生成 nfr-design-patterns.md
- [x] 3.1 P-U05-01：财务记录永久不可替换设计
- [x] 3.2 P-U05-02：Attachment 6 项强校验封装
- [x] 3.3 P-U05-03：双口径汇总实现
- [x] 3.4 P-U05-04：反向通知事件 + 部署一致性扩展
- [x] 3.5 复用 U04 FB1-FB8 守护清单（不重新评估）
- [x] 3.6 监控告警阈值（5 个 + 6 类阈值）

### Step 4 — 生成 logical-components.md
- [x] 4.1 U05 新增组件清单（含 state_machines / events / attachment_validator / repository / service / listeners）
- [x] 4.2 组件依赖图（Mermaid，含 U04 ↔ U05 双向 listener）
- [x] 4.3 4 层架构 + state_machines 子层 + listeners 独立模块
- [x] 4.4 与 U04 完整契约图（事件 / 监听 / session 共享）

### Step 5 — 提交完成消息

---

## 3. 澄清问题（请填 [Answer]）

> U05 与 U04 同模式相似度高，仅 6 个特异性问题需要确认。

### 3.1 财务记录不可替换（FB3）

**Q1**：DELETE /api/settlements/{id} 实施位置？

[Answer]: **Router 层硬编码 405**（防御深度）：

- `modules/finance/api.py` 显式声明 DELETE 端点直接抛 `HTTPException(405)`
- 不下沉到 service 层：避免后续误开放接口
- error response 统一格式：`{"code": "METHOD_NOT_ALLOWED", "message": "财务记录不可删除；请走 reject 或 V2 调整单流程"}`

```python
@router.delete("/settlements/{settlement_id}", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
async def delete_settlement_not_allowed(settlement_id: UUID) -> None:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail={
            "code": "METHOD_NOT_ALLOWED",
            "message": "财务记录不可删除；请走 reject 或 V2 调整单流程",
        },
    )
```

**Q2**：promotion 软删时的 cascade 策略？

[Answer]: **零级联** — settlement 完全独立：
- U04 promotion.soft_deactivate 不调任何 settlement 接口
- U05 端无 listener 监听 promotion soft_delete 事件
- settlement.is_active **字段不存在**（FB3）→ 无可级联字段
- audit_log 自动留痕（U04 端）+ U05 端 settlement 仍可推进状态（不受 promotion 软删影响）

测试验证：
```python
# test_settlement_promotion_soft_delete_no_cascade
async def test_settlement_unaffected_by_promotion_soft_delete():
    # Given: promotion + settlement(待付款)
    # When: U04 promotion.soft_deactivate(promotion.id)
    # Then: settlement 仍存在 + 状态保持"待付款" + 仍可走 fill_payment / mark_paid
```

### 3.2 Attachment 6 项校验封装（FB4）

**Q3**：ProofAttachmentValidator 应该放在 service 层还是 AttachmentService 内置？

[Answer]: **U05 service 层独立封装**（不放 AttachmentService 内置）：

理由：
- 通用性差 — 不同模块的 attachment 校验项不同（settlement 要 mime/size/purpose；style 详情图可能不同）
- 解耦 — AttachmentService 不应知道每个业务模块的 purpose 字符串
- 灵活演进 — V2 若 settlement 支持多张截图，校验逻辑仅在 U05 修改

实施：
- 文件：`modules/finance/attachment_validator.py`
- 类：`ProofAttachmentValidator`
- 注入：通过 `Depends(get_attachment_service)` 获取 AttachmentService 后内部封装

详细代码见 tech-stack-decisions.md §4。

**Q4**：跨租户尝试的报警分级与频率？

[Answer]: **多层防御 + 双轨报警**：

```python
# attachment_validator.py
async def validate(self, *, attachment_id, tenant_id):
    attachment = await self._service.get_by_id(attachment_id)
    if attachment is None:
        raise InvalidAttachmentReferenceError(...)  # 不暴露 attachment 是否存在
    
    if attachment.tenant_id != tenant_id:
        # 1. 指标计数
        attachment_validation_failures_total.labels(
            failure_type="tenant_mismatch", source_module="finance"
        ).inc()
        
        # 2. Sentry warning（每次都上报）
        sentry_sdk.capture_message(
            "potential_cross_tenant_attempt",
            level="warning",
            extras={
                "attachment_id": str(attachment_id),
                "expected_tenant_id": str(tenant_id),
                "user_id": str(user_id_ctx.get()),
            },
        )
        
        # 3. audit log（独立 bypass session 写）
        async with AsyncSessionBypass() as audit_session:
            await AuditService(audit_session).log(
                action="settlement.attachment_cross_tenant_attempt",
                resource="settlement",
                actor_type="anonymous",
                user_id=user_id_ctx.get(),
                after={
                    "attempted_attachment_id": str(attachment_id),
                    "user_tenant_id": str(tenant_id),
                },
            )
            await audit_session.commit()
        
        # 4. 抛 422，不暴露 attachment 详情
        raise InvalidAttachmentReferenceError(
            "attachment 不属于当前租户",
            details={"attachment_id": str(attachment_id)},
        )
```

报警阈值：
- Prometheus alert：`rate(attachment_validation_failures_total{failure_type="tenant_mismatch"}[5m]) > 0` → Sentry warning（即时）
- 每次跨租户尝试都上报（不去重）— 安全事件优先级高于性能

### 3.3 双口径汇总（FB7）

**Q5**：activity 口径需要 audit_log JOIN，性能边界与回退方案？

[Answer]:

**MVP 性能边界**：
- 10 万 settlement + 1000 当天 audit action P95 ≤ 300ms（NFR §3.1）
- audit_log 已有 `(tenant_id, action, created_at)` 索引（U01 实施）— 关键索引覆盖
- settlement 走 PK index by id

**触发性能恶化条件**：
- 单租户 settlement > 10 万
- audit_log 单日新增 > 1 万（settlement 高频活动）

**回退方案（V1+ 升级）**：
1. **第一层（无需 schema 变更）**：物化 daily_settlement_activity Materialized View，每小时 REFRESH CONCURRENTLY
2. **第二层（schema 增强）**：settlement 表新增 `last_action_at` / `last_action_type` 冗余字段（UPDATE 时同步），避免 JOIN audit_log
3. **第三层（数据归档）**：audit_log 1 年归档到 R2 后查询路径不变，活跃数据集减小

V1+ 实施触发条件：activity P95 > 500ms 持续 5min。

**实现位置**（NFR Requirements §9 已固化）：
- repository.py 内部 `daily_summary_as_of` / `daily_summary_activity` 方法（直接 SQL）
- service.py 仅做参数校验 + Pydantic 包装（无业务编排）
- api.py 两个独立 endpoint（FB7 拆开避免混合语义）

**Q6**：daily-summary 时区一致性如何保证（FB8 模式）？

[Answer]: 完全复用 U04 FB8 模式：

```python
# 所有 daily-summary 调用 service 层
async def get_daily_summary_as_of(self, *, date: date | None = None) -> DailySummaryAsOf:
    today = date or get_today()  # ← Asia/Shanghai 时区，与 U04 完全一致
    # SQL 不用 CURRENT_DATE，传 :date 参数
    return await self._repo.daily_summary_as_of(
        tenant_id=current_tenant_id(),
        date=today,
    )
```

测试覆盖：
- `test_daily_summary_at_utc_boundary` 用 freezegun + tz_offset 测边界日（UTC 23:59 vs Asia/Shanghai 次日）
- 与 U04 `test_urge_calculator_python_vs_sql_consistency` 同模式

### 3.4 反向通知事件（FB5 + 继承 U04 FB10）

**Q7**：SettlementPaid 反向 listener 注册位置？

[Answer]: **U04 端注册（modules/promotion/listeners.py）**，与 U05 端 listener 平行：

```
modules/finance/listeners.py    # U05 监听 SettlementRequested（强一致）
modules/promotion/listeners.py  # U04 监听 SettlementPaid（通知类）
```

main.py register_event_listeners 双向注册：

```python
def register_event_listeners() -> None:
    clear_handlers()
    
    # U05 → 监听 SettlementRequested（强一致正向）
    try:
        from app.modules.finance.listeners import register as register_finance
    except ModuleNotFoundError:
        log.warning("u05_finance_module_not_found...")
        return
    register_finance()
    
    # U04 → 监听 SettlementPaid（通知类反向）
    try:
        from app.modules.promotion.listeners import register as register_promotion_listeners
    except ModuleNotFoundError:
        # U04 listener 缺失也不阻塞，因 SettlementPaid required_handler=False
        log.warning("u04_promotion_listeners_not_found_skipping_reverse_sync")
        return
    register_promotion_listeners()
```

**Q8**：SettlementPaid 失败处理与 SettlementRequested 不对称如何体现？

[Answer]: 完全不对称：

| 维度 | SettlementRequested（强一致 FB1） | SettlementPaid（通知类 FB5） |
|---|---|---|
| required_handler | True | False |
| 无 handler 行为 | 抛 MissingRequiredHandlerError → 5xx | no-op + warning + 指标 |
| handler 抛异常 | 自然冒泡 → 整个事务回滚 | service 层 try/except 包装 → log + 指标 + 不阻塞主流程 |
| 部署约束 | U05 必须 ≥ U04 部署（CI gate） | 无强制约束 |
| 失败 audit | 写 `promotion.review.event_dispatch_failed`（FB5 脱敏） | 写 `settlement.paid_sync_failed`（同模式脱敏） |
| 监控指标 | `settlement_created_via_event_total{result=error}` | `settlement_paid_sync_no_match_total` |

实施代码（U05 mark_paid 末尾）：

```python
# modules/finance/service.py
async def mark_paid(self, settlement_id: UUID, payload: ..., user: User):
    # ... 状态推进 + audit
    
    # 发反向事件（通知类，failure 不阻塞主流程）
    try:
        await event_bus.dispatch(
            SettlementPaid(...),
            session=self._session,
        )
    except Exception as exc:
        # 不重新 raise（与 U04 review approve 不同）
        log.exception("settlement_paid_dispatch_failed")
        sentry_sdk.capture_exception(exc)
        await self._log_event_dispatch_failure(event, exc, user, blocking=False)
        # 不抛错，让 commit 继续
    
    await self._session.commit()
    return updated
```

注意：U04 service.review approve 失败会重新 raise（FB5 强一致）；U05 mark_paid 不重新 raise（通知类）。

### 3.5 测试约束

**Q9**：U05 测试是否需要新引入 freezegun / pytest 插件？

[Answer]: **不需要新增**，完全复用 U04 已建立的测试栈：
- freezegun（U04 已引入）
- pytest-asyncio（U01 引入）
- conftest.py 已含 `_clear_event_handlers` autouse fixture（FB6）+ `event_capture` fixture
- 新增 fixtures：`settlement_factory` / `attachment_factory`（直接复用 U04 promotion_factory 模式）

**Q10**：跨单元集成测试（U04 → U05 → U04）如何运行？

[Answer]: **同事务 fixture 注入**：

测试 fixture 模拟双向事件总线 + 真实 PostgreSQL session：

```python
# tests/conftest.py（U05 实施时追加）
@pytest_asyncio.fixture
async def cross_unit_event_bus(_clear_event_handlers) -> AsyncIterator[None]:
    """测试用：U05 + U04 listener 全部注册（与 production main.py 行为一致）."""
    from app.modules.finance.listeners import register as register_finance
    from app.modules.promotion.listeners import register as register_promotion
    register_finance()
    register_promotion()
    yield
    # cleanup 由 _clear_event_handlers autouse 处理
```

测试用例：
```python
async def test_e2e_review_approve_to_settlement_paid(
    session, cross_unit_event_bus, promotion_factory, attachment_factory, ...
):
    # Step 1: U04 review approve
    promotion = await promotion_factory.promotion(publish_status="已发布", settlement_status="待核查")
    await PromotionService(session).review(promotion.id, ReviewRequest(action="approve"), reviewer)
    
    # 断言 settlement 已创建（FB1 同事务）
    settlement = await SettlementRepository(session).find_by_promotion_id(promotion.id)
    assert settlement.settlement_status == "待核查"  # FB1 起点
    
    # Step 2-5: U05 settlement 流程
    # ... approve / fill_payment / upload_proof / mark_paid
    
    # Step 6: 断言 SettlementPaid 反向同步 promotion.settlement_status="已付款"
    await session.refresh(promotion)
    assert promotion.settlement_status == "已付款"
```

---

## 4. 决策摘要（用户填答后由 AI 整理）

无明显歧义。所有决策基于：
- INCEPTION U04+U05 同批部署 + FB1 强一致
- U04 已落地的代码契约（events.py / state_machines.py / metrics.py / register_event_listeners 框架）
- U05 functional-design 3 文档 + nfr-requirements 2 文档已固化
- 8 P1 反馈完全继承 U04（无需重新评估）

---

## 5. 与下一阶段衔接

NFR Design 完成后：
- 进入 Infrastructure Design：解决"如何部署"问题（007/008 migration chain / R2 private 桶 + signed URL TTL / Sentry 配置）
- 关键基础设施决策：
  - R2 private bucket policy（仅后端可访问）+ signed URL TTL=15min
  - settlement 表 RLS 策略（与 U04 promotion 一致）
  - Sentry alert 路由（attachment 跨租户警告 → 后端 + 安全 leader）
  - CI / staging smoke 端到端测试覆盖

---

**等待用户审阅 [Answer]，回复"继续"后进入 Step 3-4 生成 nfr-design-patterns.md + logical-components.md。**
