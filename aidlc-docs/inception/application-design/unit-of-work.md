# 工作单元（Unit of Work）

> 整合执行计划（execution-plan.md 第 9 节）+ 应用设计（components.md）+ 用户故事（stories.md），定义 23 个 sub-unit 的工作边界。

---

## 1. 三层视角

本系统的"工作单元"在三个层面有不同含义：

| 层 | 数量 | 用途 | 主要文档 |
|---|---|---|---|
| 部署单元（Deployable Service） | **6 主服务 + 1 外部 Worker** | 运行时部署边界 | execution-plan.md 第 6.2 节 |
| 逻辑模块（Logical Module） | **10 个 Epic 模块包** | 代码组织边界 | components.md 第 4 节 |
| 计划单元（Sub-Unit / UoW） | **23 个 sub-unit** | Construction 阶段批次 | 本文档 + execution-plan.md 第 9 节 |

---

## 2. 部署模型

### 2.1 Zeabur 上的 6 主服务

| 服务名 | 类型 | 职责 |
|---|---|---|
| `frontend` | Web | React 18 + Vite 构建的静态资源，绑定 app.clothinkai.com |
| `backend` | Web | FastAPI（uvicorn），绑定 api.clothinkai.com，所有同步 API |
| `celery-worker` | Worker | 处理 Celery 异步任务（导入解析、企微群发、数据采集任务调度、备份、AI） |
| `celery-beat` | Cron | Beat 定时调度器（每天 02:00 采集任务派发 / 09:00 催发扫描 / 每小时异常监控 / 03:00 备份） |
| `postgres` | 插件 | PostgreSQL 16 |
| `redis` | 插件 | Redis 7（缓存 + Celery broker / backend） |

### 2.2 外部采集 Worker（独立部署）

- **位置**：自建 Windows VM 或 Docker 主机，**不在 Zeabur**
- **通信**：HTTP pull（Q13=D 决策）
  - `POST /api/crawler/tasks/poll` — Worker 轮询拉取 pending 任务，主系统返回任务 ID + 一次性凭据
  - `POST /api/crawler/tasks/{id}/result` — Worker 完成后上传文件 + 状态
- **解耦**：Worker 不直连 PostgreSQL，仅通过 HTTPS 与主 backend 通信

#### 2.2.1 凭据安全边界（高敏感）

向 Worker 返回的解密凭据是高敏感能力，必须满足以下边界（在 U13 实施时强制）：

| 边界 | 要求 |
|---|---|
| Worker 身份 | 每个 Worker 持有专用 `worker_token`（与用户 JWT 隔离），管理员可单独签发/吊销 |
| 网络层 | 仅允许 IP allowlist 内的 Worker 访问 `/api/crawler/*`；条件允许时使用 mTLS |
| 凭据回传形式 | poll 响应中的 password 是**一次性令牌化引用**（如 `cred_token`），Worker 用 `cred_token` 在请求 RPA 平台前调用 `/api/crawler/tasks/{id}/exchange` 才换取真实明文密码；exchange 接口立即失效该 token |
| TTL | 任务签发后默认 5 分钟内必须 exchange，否则 token 过期；exchange 后明文仅在 Worker 内存使用，不写文件、不写日志 |
| 日志规范 | 整个链路（poll/exchange/result）禁止日志记录明文密码字段；日志中只记录 task_id、credential_id、masked username |
| 审计 | poll、exchange、result 三个动作各写一条 audit_log，含 worker_token_id、ip、purpose、timestamp |
| 失败自动吊销 | 同一 worker_token 连续 N 次 poll 异常（认证失败/IP 不匹配/签名异常）触发自动吊销 + 企微告警 |
| 凭据失败联动 | 采集业务失败 N 次（来自 result 上报），按 EP07-S06 策略暂停凭据，不依赖 Worker 自我裁决 |

> 这些边界与需求 12.3（不可回显）、12.4（解密审计）一致；不形成冲突，因为返回给 Worker 的不是直接明文，而是一次性令牌引用。

### 2.3 文件存储

Cloudflare R2 4 个桶：`public/` / `private/` / `credentials/` / `backups/`，按租户子路径隔离。

---

## 3. 代码组织策略（Greenfield）

### 3.1 整体目录

```
clothing-erp/
├── backend/                # FastAPI 应用，包含所有 Service
├── frontend/               # React 应用
├── docker-compose.yml      # 本地开发：postgres + redis + backend + celery-worker + celery-beat
├── .env.example
└── README.md
```

### 3.2 Backend 模块化（按 Epic 一对一）

```
backend/app/
├── core/                   # 横切（11 个组件）
├── modules/
│   ├── auth/               # EP01
│   ├── product/            # EP02
│   ├── design/             # EP03
│   ├── blogger/            # EP04
│   ├── promotion/          # EP05
│   ├── finance/            # EP06
│   ├── importer/           # EP07
│   ├── wecom/              # EP08
│   ├── report/             # EP09
│   └── ai/                 # EP11
├── services/metric/        # 跨模块指标
└── tasks/                  # Celery 任务定义
```

每个 module 内 4 层结构：`api.py` → `service.py` → `domain.py` → `repository.py` + `models.py` + `schemas.py`。

### 3.3 Frontend feature 化

```
frontend/src/features/
├── auth/ product/ design/ blogger/ promotion/
└── finance/ importer/ wecom/ report/
```

详见 `components.md` 第 2 节。

---

## 4. 23 个 Sub-Unit 详述

### 4.1 MVP 阶段（12 sub-unit）

#### U01 — 认证 + 多租户基础
- **覆盖故事**：EP01-S01, S02, S03, S04, S07, S08；EP10-NFR03（多租户隔离）；EP10-NFR04（备份与恢复，框架部分）
- **覆盖代码**：`core/db.py`, `core/tenancy.py`, `core/security/auth.py`, `core/security/permissions.py`（粗粒度部分）, `core/security/rls.py`, `core/audit.py`, `core/cache.py`, `core/config.py`, `core/celery_app.py`, `core/exceptions.py`, `core/errors.py`, `modules/auth/`, `tasks/backup_tasks.py`（每天 03:00 pg_dump → R2）
- **依赖**：— （根单元）
- **关键产出**：JWT 登录、用户/角色 CRUD、模块/功能级权限、ORM Session tenant_id 注入、PostgreSQL RLS 策略、audit_log append-only、首个数据库迁移、**备份 Celery 任务（按 NFR04 RPO/RTO/保留策略）**
- **职责边界**：备份**任务体**和**保留策略**在 U01 落地；备份**失败告警通道**（向管理员推消息）暂用日志和 Celery 重试，企微告警链路在 U07 完成后由 U07 接管（备份不依赖企微）
- **验收**：新增 2 租户，租户 A 的查询不返回租户 B 数据；登录失败 5 次触发限流；audit_log 不可修改；备份任务每天定时执行，备份文件入 R2 backups/ 桶并按 30 天/每月保留策略自动清理

#### U02 — 商品 / SKU 基础
- **覆盖故事**：EP02-S01, S02, S03, S04, S05, S06
- **覆盖代码**：`modules/product/`（不含 PlatformProduct, Bundle）
- **依赖**：U01
- **关键产出**：style/sku ORM + Repository + Service + API + Schema；款号↔商品简称双向关联
- **验收**：style_code 租户内唯一；sku.style_id 必填；按款式查 sku 返回完整列表

#### U03 — 博主库基础
- **覆盖故事**：EP04-S01, S02, S03
- **覆盖代码**：`modules/blogger/`（不含 BloggerTagService）
- **依赖**：U01
- **关键产出**：blogger CRUD + 搜索筛选；xiaohongshu_id 唯一性
- **验收**：博主搜索按昵称/类型/粉丝量过滤；编辑报价写 audit_log

#### U04 — 推广合作核心
- **覆盖故事**：EP05-S02 ~ S13
- **覆盖代码**：`modules/promotion/`，`core/state_machine.py`，`services/metric/publish_progress.py`（部分指标）
- **依赖**：U02, U03
- **关键产出**：promotion CRUD；3 个并行状态机（publish / recall / settlement_request）；UrgeStatusCalculator（Service + SQL 表达式）；爆文标记、有效点赞、CPL 计算；PR 主管审核
- **职责边界**：审核通过仅推进 `settlement_status` 到 `待核查` 并发出领域事件 `SettlementRequested(promotion_id, amount, ...)`；**不直接创建 settlement 记录**（避免与 U05 职责循环）
- **验收**：urge_status 5 种状态全部通过 GWT 验证；审核通过推送 SettlementRequested 事件，被 U05 消费

#### U05 — 财务结款核心
- **覆盖故事**：EP06-S02 ~ S08
- **覆盖代码**：`modules/finance/`（不含 OrderAdjustment, Balance），`core/attachment.py`（在 U01 已搭好基础设施，本单元首次使用 private bucket）
- **依赖**：U04
- **关键产出**：监听 `SettlementRequested` 事件 → **创建 settlement 记录**（按 promotion_id 幂等）；settlement 全流程；额外项；付款金额；付款截图上传到 R2 private 桶
- **职责边界**：U05 是 settlement 记录的**唯一创建方**和持有者；U04 只通过领域事件请求结算
- **验收**：SettlementRequested 事件触发 settlement 创建（幂等）；settlement 状态机 4 状态全部转移正确；付款字段缺失返回 422 + data_quality_issue

#### U06a — 统一导入框架
- **覆盖故事**：EP07-S07, S08, S09, S10
- **覆盖代码**：`modules/importer/`（不含具体 Adapter 和 Crawler/Credential）；`tasks/import_tasks.py` 中的 `run_import_batch`
- **依赖**：U01
- **关键产出**：upload API、import_batch / import_job / field_mapping ORM、file_hash 去重、异步解析、失败明细下载、重试策略
- **验收**：相同 hash 文件返回 409；失败行可下载 CSV；重试只跑 failed 行

#### U06b — 商品/SKU 导入适配器
- **覆盖故事**：（与 U06a 共享 EP07-S07~S10）
- **覆盖代码**：`modules/importer/adapters/style_sku.py`
- **依赖**：U02, U06a
- **关键产出**：StyleSkuImportAdapter，按 style_code/sku_code 幂等 upsert；字段映射 v1
- **验收**：
  - ✅ 上传商品 SKU 样例 CSV（含 ≥10 行有效数据），全部成功入库
  - ✅ 重复上传同一文件 → file_hash 触发 409
  - ✅ 重复行（同 style_code + sku_code）→ 幂等更新，不重复创建
  - ✅ 故意制造一条字段缺失 → 该行记入 import_job.failed，其他行成功
  - ✅ 失败明细 CSV 可下载并含 error_detail

#### U06c — 博主导入适配器
- **覆盖故事**：（共享 EP07-S07~S10）
- **覆盖代码**：`modules/importer/adapters/blogger.py`
- **依赖**：U03, U06a
- **关键产出**：BloggerImportAdapter，按 xiaohongshu_id 幂等
- **验收**：
  - ✅ 样例博主 CSV 全行成功入库
  - ✅ 重复 xiaohongshu_id 幂等更新（不创建重复博主）
  - ✅ 错误行（如 xiaohongshu_id 为空）记入失败明细
  - ✅ 重试失败行可成功

#### U06d — 推广导入适配器
- **覆盖故事**：（共享 EP07-S07~S10）
- **覆盖代码**：`modules/importer/adapters/promotion.py`
- **依赖**：U04, U06a
- **关键产出**：PromotionImportAdapter，按 internal_code 幂等
- **验收**：
  - ✅ 样例推广 CSV 全行成功入库
  - ✅ 关联不存在的 style_code → 标记 `unmatched_product` data_quality_issue
  - ✅ 关联不存在的 blogger → 标记 `unmatched_blogger`
  - ✅ 重复 internal_code 幂等更新

#### U06e — 结算导入适配器
- **覆盖故事**：（共享 EP07-S07~S10）
- **覆盖代码**：`modules/importer/adapters/settlement.py`
- **依赖**：U05, U06a
- **关键产出**：SettlementImportAdapter，按 settlement_no 幂等
- **验收**：
  - ✅ 样例结算 CSV 全行成功入库
  - ✅ 关联不存在的 promotion → 标记 `unmatched_promotion` data_quality_issue
  - ✅ 重复 settlement_no 幂等更新
  - ✅ 已付款状态的结算导入再次时不覆盖付款字段（业务保护）

#### U07 — 企微集成基础
- **覆盖故事**：EP08-S02 ~ S08
- **覆盖代码**：`modules/wecom/`（不含 S09, S10）；`tasks/wecom_tasks.py`（scan_and_dispatch_urge, execute_wecom_message）
- **依赖**：U04
- **关键产出**：WecomClient + WecomService + WecomTask 三层；外部联系人绑定；模板编辑；催发扫描定时任务；频控降级到站内通知；回调签名校验
- **职责边界**：U07 完成后，U01 的备份任务可接入企微告警通道（备份失败 push 管理员）；本接入工作量小，作为 U07 验收的附带项
- **验收**：博主当天 1 条群发限制生效；超限降级写 notification；签名失败返回 403；备份失败可通过 push_to_app 告警管理员（接入校验）

#### U08 — 发文进度看板
- **覆盖故事**：EP09-S01, S07
- **覆盖代码**：`modules/report/`（仅 PublishProgressService + 时间筛选组件）；`services/metric/publish_progress.py`（完成）
- **依赖**：U04, U05
- **关键产出**：发文进度三层看板 API；时间筛选组件（前端共用）
- **验收**：全局汇总数字与单条记录聚合一致；商品卡片按时间筛选重算；分母为 0 显示"—"

### 4.2 V1 阶段（8 sub-unit）

#### U09 — 字段级权限 + 自定义权限
- **覆盖故事**：EP01-S05, S06
- **覆盖代码**：`core/security/permissions.py`（字段级部分 + build_schema_for_user）；各模块 `permissions.py` 配置；MVP 已完成模块的 schemas.py 重构（加 `require_field` 标记）
- **依赖**：U01, U02, U05（U02/U05 含敏感字段）
- **关键产出**：字段级权限矩阵；动态 Pydantic Schema；MVP 已完成模块的回归（cost_price / quote / payment_amount 等字段按权限屏蔽）
- **验收**：设计师调用 GET /api/skus/{id} 不返回 cost_price；财务无 quote 读权限时博主详情屏蔽 quote

#### U10a — 设计制版全流程
- **覆盖故事**：EP03-S02 ~ S14
- **覆盖代码**：`modules/design/`；扩展 style 表加 design_status 状态机；`core/attachment.py`（设计稿/版型文件）；NotificationService（首次落地）
- **依赖**：U02
- **关键产出**：DesignStateMachine 7 状态 + 驳回 + 取消；3 个子表（style_fabric/style_pattern/style_craft）；自动通知下一角色
- **验收**：状态机所有合法转移通过；非法转移返回 422；驳回回退到上一状态；管理员可任意状态取消

#### U10b — 平台商品映射
- **覆盖故事**：EP02-S07
- **覆盖代码**：`modules/product/`（追加 PlatformProductService + ORM）
- **依赖**：U02
- **关键产出**：platform_product 表 + UNIQUE (tenant_id, platform, platform_id)；按 (platform, platform_id) 反查 style/sku
- **验收**：千牛商品 ID 与内部 sku 双向关联

#### U11 — 博主智能标签 + 灰豚展示
- **覆盖故事**：EP04-S04, S05, S06, S07, S08
- **覆盖代码**：`modules/blogger/tag_service.py`；`services/metric/blogger_quality.py`；`tasks/recompute_blogger_tags`
- **依赖**：U03, U13（灰豚画像数据由 U13 同步）
- **关键产出**：博主类型/阅读点赞比/假号/质量标签实时计算；阈值变更后异步重算
- **验收**：阈值修改后所有博主标记重算；分母为 0 标签返回 null

#### U12 — 平台凭据 + 采集失败告警
- **覆盖故事**：EP07-S02, S03, S04, S05, S06
- **覆盖代码**：`modules/importer/credential_service.py`；`core/security/crypto.py`；扩展 `core/audit.py`（@audit("decrypt") 装饰器）；`tasks/cleanup_expired_credentials_audit`
- **依赖**：U01
- **关键产出**：凭据 AES-256 加密存储；不可回显；解密审计；暂停/删除；失败 N 次自动暂停 + 企微告警
- **验收**：API 响应不含明文密码；解密 audit_log 写入；连续 3 次失败自动暂停

#### U13 — 自动数据采集 Worker
- **覆盖故事**：EP07-S11, S12, S13, S14
- **覆盖代码**：`modules/importer/crawler_task_service.py`；`modules/importer/adapters/qianniu.py`、`wanxiangtai.py`、`huitun.py`；`tasks/crawler_tasks.schedule_daily_tasks`；`modules/importer/data_quality_service.py`；外部 Worker 启动模板
- **依赖**：U06a, U06b, U06c, U06d, U06e, U10b（千牛商品 ID 关联）, U12
- **关键产出**：crawler_task 表；Worker pull / report API；3 个平台 Adapter；数据质量看板
- **验收**：Worker pull 拿到任务 + 解密凭据；上传文件后触发导入；data_quality_issue 按 source/severity 分组

#### U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
- **覆盖故事**：EP09-S02, S03, S04, S05
- **覆盖代码**：`modules/report/work_progress_service.py`、`target_planning_service.py`、`store_daily_service.py`、`production_service.py`；`services/metric/work_progress.py`、`store_daily.py`、`style_roi.py`；`tasks/precompute_report_cache`
- **依赖**：U05, U13
- **关键产出**：4 张报表完整实现；时间筛选 + 周环比
- **职责边界**：投产报表实现**基础口径**（用 qianniu_daily 全量数据计算）；**不在 V1 阶段做刷单剔除**（因为 order_adjustment 在 U16 才落地）。代码中预留 `exclude_brushing` 接口参数和占位逻辑（默认 false），等 U16 完成后由该单元做"刷单剔除"回归
- **验收**：投产报表口径与需求第 14.2 节指标契约一致（基础口径，不含刷单剔除）；除零返回 null；周环比正确；`exclude_brushing` 参数存在但 V1 阶段不影响结果

#### U15 — 企微进阶（发文通知 + 异常预警）
- **覆盖故事**：EP08-S09, S10
- **覆盖代码**：`modules/wecom/`（追加群机器人 + 预警推送）；`tasks/wecom_tasks.check_anomaly_and_alert`
- **依赖**：U07
- **关键产出**：发文通知 webhook；异常监控（退货率/转化率/投产比）每小时扫描
- **验收**：阈值超限 push 到管理群；阈值可在系统设置中调整

### 4.3 V2 阶段（2 sub-unit）

#### U16 — 拍单 / 刷单 / 余额
- **覆盖故事**：EP06-S09, S10, S11
- **覆盖代码**：`modules/finance/order_adjustment_service.py`、`balance_service.py`；扩展 `services/metric/style_roi.py`（启用 `exclude_brushing`）；扩展 `modules/report/production_service.py`（接入 exclude_brushing 默认改为 true）
- **依赖**：U05（为基础数据）；本单元末尾做对 U14 投产报表的回归增强
- **关键产出**：order_adjustment + exclude_from_roi 字段；金额表达式解析；余额自动计算；**对 U14 投产报表的刷单剔除回归（核心增强）**
- **验收**：投产报表 ROI 计算自动剔除 `exclude_from_roi=true` 的订单；余额不一致拒绝保存

#### U17 — 套装 + BI 看板 + 报表导出
- **覆盖故事**：EP02-S08, EP09-S06, EP09-S08
- **覆盖代码**：`modules/product/bundle_service.py`；`modules/report/bi_service.py`、`export_service.py`
- **依赖**：U02, U14
- **关键产出**：bundle_product + bundle_item；BI 卡片 + 图表组合；Excel 导出（按报表类型 + 时间筛选 + 衍生字段）
- **验收**：bundle 销量按 bundle_item 拆分；导出文件可被 Excel 打开

### 4.4 P3 阶段（1 sub-unit）

#### U18 — AI 决策建议
- **覆盖故事**：EP11-S01, S02, S03
- **覆盖代码**：`modules/ai/`；`tasks/ai_advisory_request`
- **依赖**：U14
- **关键产出**：DeepSeek V4 集成；策略建议/异常归因/博主选择
- **验收**：AI 服务不可用时返回 503 不阻塞页面；ai_advice_log 留痕

---

## 5. 工作单元生命周期（Construction 阶段）

每个 sub-unit 在 Construction 阶段按以下流程：

```
单元启动
    ↓
Functional Design（条件执行）         ← 状态机、计算逻辑、跨表指标必做
    ↓ user approval
NFR Requirements（条件执行）         ← 性能/安全/多租户落点
    ↓ user approval
NFR Design（条件执行）               ← RLS、加密、字段级权限、频控算法
    ↓ user approval
Infrastructure Design（条件执行）    ← Zeabur 服务、R2 桶、Celery 队列
    ↓ user approval
Code Generation Part 1 - Planning   ← 列出文件清单、迁移、测试
    ↓ user approval
Code Generation Part 2 - 执行       ← 生成代码 + 测试
    ↓ user approval
单元自身单元/集成测试通过
    ↓
单元完成
```

**阶段末**（MVP / V1 / V2 / P3 各自结束时）追加一次 Build & Test，做跨单元集成回归。

---

## 6. 阶段批次

| 阶段 | sub-units | 阶段末交付 | 阶段末验收门 |
|---|---|---|---|
| MVP | U01, U02, U03, U04, U05, U06a, U06b, U06c, U06d, U06e, U07, U08（共 12） | 可登录的全栈系统：商品/SKU/博主/推广/结算/手动导入/企微催发/发文进度 | 跨单元数据流通；催发状态端到端；企微频控降级；多租户隔离回归；性能 P95 ≤ 500ms 抽样 |
| V1 | U09, U10a, U10b, U11, U12, U13, U14, U15（共 8） | 字段级权限、设计制版、自动采集、智能博主标签、投产报表、企微进阶 | 设计制版状态机端到端；自动采集 → 入库 → 报表全链路；字段级权限回归（重点）；凭据加密 + 审计 |
| V2 | U16, U17（共 2） | 拍单刷单余额、套装、BI、导出 | 刷单 ROI 隔离回归；余额平账；BI 看板和导出 |
| P3 | U18（共 1） | AI 决策建议 | 不阻塞前阶段；服务降级测试 |

**关键路径**：
- MVP：U01 → U02 → U04 → U05 → U07 → U08
- V1：U10a + U13 都依赖前序，任一阻塞会延后 V1
