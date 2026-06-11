# U05 基础设施设计计划（Infrastructure Design Plan）

> 单元：U05 — 财务结款核心  
> 范围：U05 特异性基础设施增量；通用基础设施全部继承 U01 + shared-infrastructure  
> 关键差异：**首次使用 R2 private 桶**（付款截图，FB4）

---

## 1. 单元上下文

### 1.1 与 U01-U04 基础设施的关系

U01 已建立完整 6 服务部署 + PG + Redis + R2 4 桶 + Sentry + GitHub Actions。  
U02 启用 pg_trgm 扩展。  
U04 引入 core/events.py 事件总线 + register_event_listeners 框架 + CI validate-event-listeners job + e2e-smoke-after-deploy 框架。

**U05 增量**：
- PostgreSQL：3 张新表（settlement / settlement_extra_item / settlement_sequence）+ 12 个索引（含 GIN trgm + 永久 UNIQUE，FB3）+ 2 条 RLS 策略
- **R2 private 桶首次使用**（付款截图，FB4）— U01 已 provisioning + bucket policy 配置好，U05 仅消费
- Migration chain：007_u05_create_settlement_tables + **008_u05_backfill_settlements**（FB8 独立 migration）
- Sentry：新增 `module=finance` tag + 跨租户告警分级
- Prometheus：新增 5 个自定义指标 + 6 类告警阈值
- main.py：扩展 register_event_listeners 双向加载（U05 finance + U04 promotion 反向 listener）

### 1.2 关键部署一致性约束（继承 U04 FB10 + U05 扩展）
- migration 007 + 008 同批 alembic upgrade（007 创建表 + 008 backfill）
- staging e2e-smoke-after-deploy 强制：跑完整 J4 旅程（U04 review approve → U05 settlement 创建 → approve → fill_payment → upload_proof → mark_paid → SettlementPaid 同步）
- CI grep 检查：`from app.modules.finance.listeners import register` 必须命中（U04 batch 4 已实施）
- CI grep 检查（V1+ 可选）：`from app.modules.promotion.listeners import register` 命中（缺失不阻塞，因 SettlementPaid required_handler=False）
- 启动时 register_finance 失败 → fail fast；register_promotion_listeners 失败也 fail fast（如果模块存在但内部错误）

### 1.3 输入文档
- U05 functional-design 3 文档
- U05 nfr-requirements 2 文档
- U05 nfr-design 2 文档
- U01 infrastructure-design + shared-infrastructure（参考通用基线）
- U04 infrastructure-design + deployment-architecture（参考事件总线 + e2e-smoke 框架）

### 1.4 输出文档
- `U05/infrastructure-design/infrastructure-design.md`（资源清单 + PG 增量 + R2 path + Sentry + Prometheus）
- `U05/infrastructure-design/deployment-architecture.md`（007/008 migration + 双批部署流程 + 端到端 smoke + 回滚预案）

---

## 2. 计划步骤

### Step 1 — 分析设计文档
- [x] 1.1 读取 U05 7 份设计文档
- [x] 1.2 与 U04 部署模板对齐（U04 已搭好 e2e-smoke 框架）

### Step 2 — 创建本计划
- [x] 2.1 列出 U05 增量
- [x] 2.2 列出澄清问题（已预填）
- [x] 2.3 等待用户填答 [Answer]

### Step 3 — 生成 infrastructure-design.md
- [x] 3.1 资源清单（无新增 Zeabur 服务）
- [x] 3.2 PostgreSQL 增量（3 表 + 12 索引 + 2 RLS + 008 backfill）
- [x] 3.3 R2 private 桶 path 规划 + signed URL TTL（FB4）
- [x] 3.4 Sentry tag 增量 + 告警路由（含跨租户 attachment warning）
- [x] 3.5 Prometheus 指标增量（5 个）+ 告警阈值（6 类）
- [x] 3.6 与 shared-infrastructure 对齐（attachment 引用计数 V1 路径）

### Step 4 — 生成 deployment-architecture.md
- [x] 4.1 Migration 007 完整代码（建表 + 索引 + RLS）
- [x] 4.2 Migration 008 完整代码（FB8 backfill PL/pgSQL）
- [x] 4.3 双批部署流程（U04+U05 同批，含 deploy-staging e2e-smoke 启用）
- [x] 4.4 验证清单 + 端到端 smoke test 完整脚本
- [x] 4.5 回滚预案（007 / 008 downgrade 不可逆）
- [x] 4.6 main.py register_event_listeners 双向扩展配置

### Step 5 — 完成消息

---

## 3. 澄清问题（请填 [Answer]）

### 3.1 R2 / 桶 / signed URL（FB4 首次使用 private 桶）

**Q1**：R2 private 桶 path 规划？

[Answer]: 复用 U01 已规划的 attachment 框架 path：

```
{bucket_private}/
└── {tenant_id}/
    └── settlements/
        └── proof/
            └── {attachment_id}/
                └── {filename}
```

具体生成由 AttachmentService 完成（U05 不直接操作 R2 path）。

**Q2**：signed URL TTL？

[Answer]: **15 分钟（900s）** — 与 U01 attachment private 桶基线一致。

理由：
- 财务在结算页查看截图通常 < 1 分钟，15min 足够
- 短 TTL 降低签名 URL 泄露风险
- V1+ 评估按角色差异化（如 admin 可申请 1h TTL）

**Q3**：R2 private 桶 policy 是否需要修改？

[Answer]: **不需要修改**。U01 已配置：
- 仅后端服务可访问（IAM access key 持有人）
- 公网无法直接访问
- 通过 signed URL 才能下载（CloudFront / R2 native signing）

U05 仅消费现有 policy，无 policy 变更。

### 3.2 PostgreSQL / 角色 / 队列

**Q4**：是否需要新增 PostgreSQL 角色？

[Answer]: **不需要**。沿用 U01 三个角色（clothing_app / clothing_bypass / clothing_archiver）。

settlement 表需要：
- clothing_app：通过 RLS 限制只看本租户（应用层默认）
- clothing_bypass：跨租户 audit 写入（attachment 跨租户告警时使用 AsyncSessionBypass）
- clothing_archiver：审计归档任务（U01 已支持）

**Q5**：是否需要新增 Celery 队列？

[Answer]: **不需要 MVP 阶段**。

事件总线本地同事务执行（FB1 强一致），无 Celery 任务。

**V1 阶段**新增（不在本单元 scope）：
- `finance_reconcile` 队列：跑 reconcile_promotion_settlement_status 任务（每天凌晨 03:00），同步 promotion 与 settlement 状态（FB5 兜底）
- 配置位置：`tasks/finance_reconcile.py` + `core/celery_app.py` 注册

### 3.3 部署强约束（继承 U04 FB10）

**Q6**：U04+U05 同批部署的 CI/CD 强制策略？

[Answer]: 完全继承 U04 的 5 层防护，本单元只新增 staging e2e-smoke 启用：

| 层 | U04 已实施 | U05 实施时新增 |
|---|---|---|
| 同 PR / migration 同批 | ✅ 文档约束 | 维持 |
| CI grep finance.listeners | ✅ U04 batch 4 已 | 维持 |
| staging e2e-smoke 框架 | ✅ U04 batch 4 placeholder | **启用真实端到端 smoke**（详见 Q9） |
| 启动检查 register_finance fail fast | ✅ U04 batch 4 已 | 维持 |
| 启动检查 register_promotion_listeners | — | **新增**（缺失只 warning，因通知类） |
| 文档明确同批部署 | ✅ U04 已 | 在 deployment-architecture 重申 + 加 J4 端到端流程 |

### 3.4 Migration 链与 backfill

**Q7**：007 与 008 migration 是否独立部署？

[Answer]: **同一 alembic upgrade chain，但分两个文件**：

```
006_u04_create_promotion_tables.py    (U04 已部署)
007_u05_create_settlement_tables.py   (U05 实施)
008_u05_backfill_settlements.py       (U05 实施，独立 migration，FB8)
```

执行流程：
- `alembic upgrade head` 一次性升 007 + 008
- 不分两次执行 — 避免 007 升完但 008 未执行的窗口期内的状态不一致

**Q8**：008 backfill 失败如何回滚？

[Answer]: **downgrade 不可逆**（财务数据保护）。

详细策略：
- 008 downgrade 抛 `RuntimeError("backfill migration is not reversible")`
- 真实失败场景：
  - 008 部分执行后崩溃 → 通过 audit_log 反查已 INSERT 的 settlement 行 → admin 手动审计后清理
  - 008 完全失败（007 已升）→ 业务恢复后手动重跑 008 PL/pgSQL（幂等：`NOT EXISTS (SELECT 1 FROM settlement WHERE promotion_id=p.id)` 防重复）
- 008 内部使用 `IF NOT EXISTS` + audit 留痕（极端场景下可重跑）

测试策略：
- staging 跑 dry-run（一个隔离 tenant）验证 backfill 行数 = 0（FB1 强一致预期）

### 3.5 端到端 smoke test

**Q9**：staging deploy 后的 e2e-smoke 测试细节？

[Answer]: U04 batch 4 已搭好框架（占位 placeholder），U05 实施时启用真实 J4 端到端测试：

```bash
#!/bin/bash
# .github/workflows/deploy-staging.yml::e2e-smoke-after-deploy

# 1. 等待 staging 部署完成（最多 5 分钟）
for i in {1..30}; do
  if curl -fsS https://api-staging.clothinkai.com/health 2>/dev/null; then
    break
  fi
  sleep 10
done

# 2. 准备测试数据（pre-seeded promotion at "已发布" + "待核查"）
TOKEN=$(curl -sS https://api-staging.clothinkai.com/api/auth/login \
  -d '{"username":"smoke_test_pr_manager","password":"<from_secret>"}' | jq -r .access_token)

PROMOTION_ID=$(curl -sS -H "Authorization: Bearer $TOKEN" \
  https://api-staging.clothinkai.com/api/promotions/?publish_status=已发布&limit=1 \
  | jq -r '.items[0].id')

# 3. U04 review approve → 应该触发 SettlementRequested → U05 创建 settlement
curl -sS -X POST https://api-staging.clothinkai.com/api/promotions/$PROMOTION_ID/review \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"action":"approve"}' --fail || exit 1

# 4. 验证 U05 端 settlement 已创建
SETTLEMENT_ID=$(curl -sS -H "Authorization: Bearer $TOKEN" \
  https://api-staging.clothinkai.com/api/settlements/?promotion_id=$PROMOTION_ID \
  | jq -r '.items[0].id')

if [ "$SETTLEMENT_ID" = "null" ]; then
  echo "::error::FB1 强一致失败：U04 review approve 未创建 U05 settlement"
  exit 1
fi

# 5. 验证 settlement_status="待核查"（FB1）
STATUS=$(curl -sS -H "Authorization: Bearer $TOKEN" \
  https://api-staging.clothinkai.com/api/settlements/$SETTLEMENT_ID \
  | jq -r .settlement_status)

if [ "$STATUS" != "待核查" ]; then
  echo "::error::FB1 状态口径错误：起点应为'待核查'，实际为'$STATUS'"
  exit 1
fi

# 6. cleanup：reject 推回避免污染（避免后续 smoke 影响）
curl -sS -X PUT https://api-staging.clothinkai.com/api/settlements/$SETTLEMENT_ID/review \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"action":"reject","review_reason":"smoke test cleanup"}' --fail
```

**注**：完整 J4 旅程（review → fill_payment → upload_proof → mark_paid → SettlementPaid）的 smoke 太重，留 V1 实施。MVP smoke 仅覆盖 FB1 强一致核心。

**Q10**：production 部署的 staging 强制约束？

[Answer]: deploy-staging.yml::e2e-smoke-after-deploy 失败 → **阻止 production 部署**：

```yaml
# .github/workflows/deploy-prod.yml
deploy:
  name: Deploy to production
  needs: [check-staging-smoke-passed]
  runs-on: ubuntu-latest
  steps:
    # check-staging-smoke-passed job 检查最近一次 staging smoke 状态
    # 失败 → 整个 workflow 失败
    ...
```

**Q11**：smoke 测试的测试数据如何准备？

[Answer]: 
- **方案 A（推荐）**：staging tenant 内预先种子化的 dummy promotion 池（10-20 个），smoke 测试每次随机挑选一个未结算的
  - seed migration：009_u05_seed_smoke_test_data.py（仅 staging 环境运行，production 跳过）
  - 池子用完后定期重新种子化（V1 自动化）
- **方案 B**：每次 smoke 临时创建 promotion + cleanup
  - 简单但 cleanup 失败时污染数据
  - MVP 阶段不采用

[Answer 决定]：选 **方案 A** — staging 专用预置数据 + V1 自动化补充

### 3.6 Sentry 告警路由

**Q12**：跨租户 attachment 告警接收方？

[Answer]: 双轨道：
- **后端 leader** — 即时通知（攻击 / 越权疑似 → 紧急排查）
- **安全 leader** — 抄送（合规视角，月度统计）

实施：Sentry alert rule 设置 `tag:source_module=finance` + `level=warning` + `message="potential_cross_tenant_attempt"` → assign to "backend_leader" group + cc "security_leader"。

### 3.7 监控仪表盘（V1 实施）

**Q13**：U05 是否需要专属 Grafana 仪表盘？

[Answer]: MVP 阶段**不实施**专属仪表盘（U01 已建立的通用仪表盘已包含所有 Prometheus 指标）。

V1 实施时新增：
- 财务结算专属仪表盘（按 settlement_status 分布 + 平均周期 + 跨租户告警热力图）
- 反向同步监控仪表盘（settlement_paid_sync_no_match + reconcile 任务执行情况）

---

## 4. 决策摘要（用户填答后由 AI 整理）

无明显歧义。所有决策基于：
- INCEPTION U04+U05 同批部署 + FB1 强一致
- U01 R2 4 桶基础设施已 provisioning（U05 首次消费 private 桶但无 policy 变更）
- U04 batch 4 已落地的 e2e-smoke 框架（U05 实施时启用真实端到端测试）
- 8 P1 反馈完全继承 U04（无需重新评估）
- 财务数据合规要求（不可替换 + 跨租户告警 + 008 不可逆）

---

## 5. 与下一阶段衔接

Infrastructure Design 完成后：
- 进入 Code Generation：所有设计已稳定，可以生成生产可用的代码
- U05 Code Generation 预估范围：
  - Python 业务代码（modules/finance/）：约 17 文件
  - Python 横切修改：3 modified（main.py / metrics.py / 新增 modules/promotion/listeners.py）
  - Alembic migration：2（007 + 008）
  - Python 测试：约 22（5 unit + 17 integration + 1 api + 2 performance + conftest 修改）
  - TypeScript 前端：2 文件
  - 文档摘要：3
  - CI/CD 修改：2（启用真实 e2e-smoke + 可选追加 promotion.listeners grep）
  - **U05 总计预估**：约 47 新文件 + 5 修改

---

**等待用户审阅 [Answer]，回复"继续"后进入 Step 3-4 生成 2 份 Infrastructure Design 文档。**
