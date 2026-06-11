# 组件设计（Components）

> 基于 Q1=B 四层 / Q2=A 按 Epic 一包 / Q3=B features 组织等决策生成。每个组件标注覆盖的故事和工作单元归属。

## 1. 后端总体结构

```
backend/
├── app/
│   ├── main.py                  # FastAPI 入口
│   ├── core/                    # 横切关注点
│   │   ├── config.py            # 配置（环境变量、租户、密钥）
│   │   ├── db.py                # SQLAlchemy 引擎、Session、tenant_id 注入
│   │   ├── cache.py             # Redis 客户端
│   │   ├── celery_app.py        # Celery 应用 + Beat 调度
│   │   ├── security/
│   │   │   ├── auth.py          # JWT 编解码、密码哈希
│   │   │   ├── permissions.py   # RBAC + 字段级权限装饰器
│   │   │   ├── crypto.py        # AES-256 凭据加解密
│   │   │   └── rls.py           # PostgreSQL RLS 策略
│   │   ├── tenancy.py           # 多租户中间件（注入 tenant_id 到 Session）
│   │   ├── audit.py             # AuditService + @audit 装饰器
│   │   ├── attachment.py        # AttachmentService（R2 公私桶 + 签名 URL）
│   │   ├── state_machine.py     # 状态机基类 + 转移表
│   │   ├── exceptions.py        # 统一异常体系
│   │   └── errors.py            # 错误码与响应包装
│   ├── modules/                 # 业务模块（按 Epic 一对一）
│   │   ├── auth/                # EP01
│   │   ├── product/             # EP02
│   │   ├── design/              # EP03
│   │   ├── blogger/             # EP04
│   │   ├── promotion/           # EP05
│   │   ├── finance/             # EP06
│   │   ├── importer/            # EP07
│   │   ├── wecom/               # EP08
│   │   ├── report/              # EP09
│   │   └── ai/                  # EP11
│   ├── services/
│   │   └── metric/              # 跨模块指标服务
│   └── tasks/                   # Celery 任务定义（按业务分文件）
│       ├── import_tasks.py
│       ├── wecom_tasks.py
│       ├── crawler_tasks.py     # 采集任务派发
│       └── backup_tasks.py
├── alembic/                     # 数据库迁移
├── tests/
│   ├── unit/
│   ├── integration/
│   └── api/
├── pyproject.toml
└── Dockerfile
```

### 每个 module 内部结构（4 层）

```
modules/promotion/                # 以 EP05 为例
├── __init__.py
├── api.py                        # Router 层：FastAPI APIRouter，仅做协议转换
├── service.py                    # Service 层：编排，调用 Domain 和 Repository
├── domain.py                     # Domain 层：纯业务对象 + 状态机方法（无 ORM 依赖）
├── repository.py                 # Repository 层：SQLAlchemy 查询封装
├── models.py                     # SQLAlchemy ORM 模型
├── schemas.py                    # Pydantic 请求/响应 Schema（含字段级动态变体）
├── permissions.py                # 该模块的权限定义和装饰器
└── exceptions.py                 # 模块特定异常
```

---

## 2. 前端总体结构

```
frontend/
├── src/
│   ├── main.tsx                  # 入口
│   ├── App.tsx                   # 路由 + 全局 Provider
│   ├── pages/                    # 路由壳（仅做组合）
│   │   ├── LoginPage.tsx
│   │   ├── PromotionsPage.tsx
│   │   └── ...
│   ├── features/                 # 业务功能（按 Epic 组织）
│   │   ├── auth/                 # EP01
│   │   │   ├── components/
│   │   │   ├── hooks/
│   │   │   ├── api.ts            # 该 feature 的 API 调用
│   │   │   └── types.ts
│   │   ├── product/              # EP02
│   │   ├── design/               # EP03
│   │   ├── blogger/              # EP04
│   │   ├── promotion/            # EP05
│   │   ├── finance/              # EP06
│   │   ├── importer/             # EP07
│   │   ├── wecom/                # EP08
│   │   └── report/               # EP09
│   ├── components/               # 跨 feature 共用
│   │   ├── AppLayout/
│   │   ├── DateRangePicker/      # 时间筛选组件 (EP09-S07)
│   │   ├── PermissionGate/       # 权限守卫
│   │   ├── AttachmentUpload/
│   │   └── ...
│   ├── stores/                   # Zustand 全局状态
│   │   ├── authStore.ts          # 当前用户、token、权限
│   │   └── uiStore.ts            # 主题、侧边栏、通知
│   ├── services/                 # 跨 feature 共用
│   │   ├── apiClient.ts          # Axios 实例 + JWT 拦截器
│   │   └── queryClient.ts        # React Query 配置
│   ├── hooks/                    # 跨 feature 共用 hooks
│   ├── utils/
│   └── types/                    # 全局共用类型
├── package.json
└── vite.config.ts
```

---

## 3. 横切组件（app/core/）

### 3.1 多租户隔离（tenancy.py）
**职责**：FastAPI 中间件读取 JWT 中 `tenant_id` 后设置 SQLAlchemy Session 属性 `info["tenant_id"]`，所有 ORM 查询通过 `before_compile` 事件钩子自动 WHERE 注入 `tenant_id`。配合 PostgreSQL RLS 兜底。

**接口**：
- `tenancy_middleware(request) -> tenant_id`
- `set_tenant_context(session, tenant_id)`
- `clear_tenant_context(session)`

**覆盖**：EP01-S07、EP10-NFR03、所有业务模块（横切）  
**单元归属**：U01

### 3.2 字段级权限（security/permissions.py）
**职责**：基于 Q7=D 决策，提供两套机制：
1. **装饰器** `@require_permission("scope:write")`：装饰 Router 方法做粗粒度授权
2. **Pydantic 动态 Schema** `build_schema_for_user(base_schema, user)`：根据用户字段权限动态生成响应 Schema，屏蔽无权限字段

**接口**：
- `require_permission(scope: str, action: str = "read")` → FastAPI Depends
- `require_field(field_name: str)` → Pydantic Field 标记
- `build_schema_for_user(base_schema_cls, user) -> type[BaseModel]`
- `check_permission(user, scope, action) -> bool`

**覆盖**：EP01-S04~S06、EP10-NFR02  
**单元归属**：U01（粗粒度）+ U09（字段级）

### 3.3 凭据加密（security/crypto.py）
**职责**：AES-256 凭据加解密，按租户独立密钥（密钥从环境变量或 KMS 取）。每次解密写 audit_log。

**接口**：
- `encrypt_credential(tenant_id, plaintext) -> ciphertext`
- `decrypt_credential(tenant_id, credential_id, ciphertext, purpose) -> plaintext`
- `rotate_tenant_key(tenant_id) -> bool`

**覆盖**：EP07-S03、EP07-S04  
**单元归属**：U12

### 3.4 状态机基类（state_machine.py）
**职责**：基于 Q8=C 决策，提供领域模型状态机基类 + 转移表。每个状态转移声明在常量文件中，`StateMachine.transition()` 校验合法性。

**接口**：
- `class StateMachine`：基类，含 `current_state`、`transition(action, **kwargs)`、`can_transition(action)`
- `class TransitionTable`：声明式转移定义（from_state、action、to_state、guard、side_effects）

**覆盖**：EP03（设计制版 7 状态）、EP05（推广合作 3 个并行状态机）、EP06（结算 4 状态）  
**单元归属**：U04、U05、U10a

### 3.5 审计日志（audit.py）
**职责**：基于 Q14=D 决策，提供两套机制：
1. **API 装饰器** `@audit("operation_name")`：装饰 Router 方法
2. **ORM 事件钩子**：监听敏感表（user、credential、settlement、style、sku）的 INSERT/UPDATE 自动写

**接口**：
- `audit(operation: str, resource: str | None = None)` → 装饰器
- `register_audit_listeners(model: Base)` → 注册 ORM 钩子
- `AuditService.log(tenant_id, user_id, action, resource, ...)` → 显式调用

**约束**：audit_log 表 append-only（数据库层 REVOKE UPDATE/DELETE）  
**覆盖**：EP01-S08、EP07-S04  
**单元归属**：U01

### 3.6 附件管理（attachment.py）
**职责**：基于 Q11=C 决策，统一管理 R2 上传/下载/签名 URL。业务表通过 `attachment_id` 关联到 attachment 表，attachment 表记录 bucket（public/private）和 path。

**接口**：
- `AttachmentService.upload(tenant_id, file, bucket: Literal["public", "private"]) -> attachment_id`
- `AttachmentService.get_url(attachment_id, expires_in: int = 900) -> url`
- `AttachmentService.delete(attachment_id) -> bool`

**桶规划**（per requirements 第 3.6 节）：
- `public/`：商品图片、设计稿（CDN 公开）
- `private/`：付款截图、订单截图、买家秀（签名 URL，15 分钟过期）
- `credentials/`：加密凭据备份（仅后端）
- `backups/`：数据库备份

**覆盖**：EP02-S01（主图）、EP03-S02（设计稿）、EP03-S04（制版文件）、EP06-S07（付款截图）  
**单元归属**：U01（基础设施）+ 各业务单元使用

---

## 4. 业务模块（modules/）

### 4.1 auth — EP01 认证与权限
**职责**：用户登录、密码管理、用户 CRUD、角色分配、自定义权限、审计日志查询  
**关键组件**：
- `AuthService`：登录、刷新 token、修改密码
- `UserService`：用户 CRUD、角色分配
- `PermissionService`：自定义权限、字段级权限矩阵
- `AuditService`：审计日志查询（写入在 core/audit.py）

**Domain 对象**：`User`、`Role`、`Permission`、`UserPermissionMatrix`  
**ORM 模型**：`user`、`role`、`permission`、`user_role`、`user_permission`、`audit_log`  
**覆盖故事**：EP01-S01~S08  
**单元归属**：U01（S01-S04, S07-S08）+ U09（S05-S06）

### 4.2 product — EP02 商品与 SKU
**职责**：款式、SKU、平台商品映射、套装/组合的 CRUD 与查询  
**关键组件**：
- `StyleService`、`SkuService`、`PlatformProductService`、`BundleService`
- `StyleRepository`、`SkuRepository`、`PlatformProductRepository`、`BundleRepository`

**Domain 对象**：`Style`、`Sku`、`PlatformProduct`、`Bundle`、`BundleItem`  
**ORM 模型**：`style`、`sku`、`platform_product`、`bundle_product`、`bundle_item`  
**覆盖故事**：EP02-S01~S08  
**单元归属**：U02（S01-S06）+ U10b（S07）+ U17（S08）

### 4.3 design — EP03 设计制版
**职责**：设计制版全流程状态机（7 状态 + 驳回 + 取消）  
**关键组件**：
- `DesignService`：编排状态转移和通知
- `DesignDomain`：含状态机方法（`submit_fabric`、`submit_pattern`、`grade`、`reject`、`cancel`...）
- `DesignRepository`

**Domain 对象**：`DesignStateMachine`（基于 core/state_machine.py）  
**ORM 模型**：复用 `style`，新增 `style_fabric`、`style_pattern`、`style_craft`  
**状态机**：转移表见 `modules/design/state_machine.py`  
**覆盖故事**：EP03-S02~S14（S01 是 Overview 不实施）  
**单元归属**：U10a

### 4.4 blogger — EP04 博主库与智能标签
**职责**：博主 CRUD、搜索、智能字段计算（博主类型/阅读点赞比/假号/质量标签）、灰豚画像展示  
**关键组件**：
- `BloggerService`、`BloggerTagService`（计算字段聚合）
- `BloggerRepository`

**Domain 对象**：`Blogger`、`AudienceProfile`（来自灰豚同步）  
**ORM 模型**：`blogger`、`wecom_contact`（绑定企微外部联系人）  
**覆盖故事**：EP04-S01~S08  
**单元归属**：U03（S01-S03）+ U11（S04-S08）

### 4.5 promotion — EP05 推广合作
**职责**：推广合作生命周期管理（创建→催发→发布→召回→审核）  
**关键组件**：
- `PromotionService`：编排
- `PromotionDomain`：3 个并行状态机（publish / recall / settlement）
- `UrgeStatusCalculator`：实时计算 urge_status（Q10=D 决策，Service 层 + SQL 表达式两种实现）
- `PromotionRepository`

**Domain 对象**：`Promotion`、`PublishStateMachine`、`RecallStateMachine`、`SettlementStateMachine`  
**ORM 模型**：`promotion`  
**覆盖故事**：EP05-S02~S13（S01 是 Overview）  
**单元归属**：U04

### 4.6 finance — EP06 财务结款
**职责**：结算单全流程、拍单、刷单、余额核对  
**关键组件**：
- `SettlementService`、`OrderAdjustmentService`、`BalanceService`
- `SettlementDomain`、`SettlementStateMachine`
- 各 Repository

**Domain 对象**：`Settlement`、`OrderAdjustment`、`BalanceRecord`  
**ORM 模型**：`settlement`、`settlement_extra_item`、`order_adjustment`、`balance_record`  
**覆盖故事**：EP06-S02~S11  
**单元归属**：U05（S02-S08）+ U16（S09-S11）

### 4.7 importer — EP07 数据采集与统一导入
**职责**：导入框架（手动 + 自动）+ 凭据管理 + 采集 Worker 任务队列  
**关键组件**：
- `ImportService`：批次管理（创建、状态追踪、重试）
- `ImportAdapterRegistry`：注册商品/博主/推广/结算等业务适配器
- `FieldMappingService`：字段映射版本管理
- `CredentialService`：凭据 CRUD（含加密）+ 暂停/删除/告警
- `CrawlerTaskService`：采集任务派发与回收（Q13=D Pull 模型）
  - `POST /api/crawler/tasks/poll`：Worker 轮询拉任务
  - `POST /api/crawler/tasks/{id}/result`：Worker 上传结果
- `ImportRepository`

**Domain 对象**：`ImportBatch`、`ImportJob`、`FieldMapping`、`Credential`、`CrawlerTask`  
**ORM 模型**：`import_batch`、`import_job`、`field_mapping`、`credential`、`data_quality_issue`、`crawler_task`  
**覆盖故事**：EP07-S02~S14  
**单元归属**：U06a 框架 + U06b/c/d/e 适配器（MVP）+ U12 凭据 + U13 采集 Worker（V1）

### 4.8 wecom — EP08 企业微信集成
**职责**：企微 SDK 封装 + 业务编排 + 异步任务  
**关键组件**（Q12=A 三层）：
- `WecomClient`：低层 SDK，封装企微 REST API（access_token 管理、群发、回调签名校验）
- `WecomService`：业务编排（绑定外部联系人、模板渲染、频控判断）
- `WecomTask`（在 `app/tasks/wecom_tasks.py`）：Celery 任务（自动催发扫描、群发执行、降级写通知）
- `WecomConfigService`：企微应用配置
- `WecomMessageService`：消息状态查询

**Domain 对象**：`WecomMessage`、`MessageTemplate`  
**ORM 模型**：`wecom_message`、`wecom_config`、`message_template`  
**覆盖故事**：EP08-S02~S10  
**单元归属**：U07（S02-S08）+ U15（S09-S10）

### 4.9 report — EP09 报表与看板
**职责**：发文进度三层看板、工作进度、爆款约篇、店铺数据、投产报表、BI、导出  
**关键组件**：
- `ReportService`：报表组装（调用 MetricService 取指标）
- `PublishProgressService`：发文进度三层
- `WorkProgressService`：工作进度表
- `StoreDailyService`：店铺数据
- `ProductionService`：投产报表
- `ReportExportService`：Excel 导出
- 各 Repository

**Domain 对象**：`ReportSnapshot`、`TimeRange`  
**ORM 模型**：复用各业务表 + `target_planning`（爆款约篇目标）  
**覆盖故事**：EP09-S01~S08  
**单元归属**：U08（S01, S07）+ U14（S02-S05）+ U17（S06, S08）

### 4.10 ai — EP11 AI 决策建议（P3）
**职责**：DeepSeek V4 集成，提供策略/归因/选博主建议  
**关键组件**：
- `DeepSeekClient`：底层 API 调用
- `AiAdvisoryService`：业务编排（数据准备、prompt 构造、降级策略）

**ORM 模型**：`ai_advice_log`（请求/响应留痕）  
**覆盖故事**：EP11-S01~S03  
**单元归属**：U18

---

## 5. 跨模块服务（services/metric/）

### MetricService（Q9=A 单独包）
**职责**：所有报表指标的统一计算入口。每个指标一个函数，可按租户/时间/款式/PR 调用。

**接口**：
```python
metric/
├── publish_progress.py    # 约篇量、发布量、超时率、点赞成本
├── work_progress.py       # PR 月度 KPI
├── style_roi.py           # 净投产比、退货率、加购成本
├── store_daily.py         # 店铺日报字段
├── blogger_quality.py     # 博主质量指标
├── data_quality.py        # 数据质量统计
└── common.py              # 共用工具（除零保护、空值处理）
```

**覆盖故事**：EP04-S04~S07、EP05-S10~S12、EP09-S01~S05  
**单元归属**：U04（推广指标）+ U08（发文进度）+ U11（博主质量）+ U14（其他报表）

---

## 6. 前端 Features 详细

每个 feature 包结构：
```
features/promotion/                # 以 EP05 为例
├── components/                    # 该 feature 专用组件
│   ├── PromotionList.tsx
│   ├── PromotionForm.tsx
│   ├── UrgeStatusBadge.tsx
│   └── ...
├── hooks/                         # 该 feature 的 hooks
│   ├── usePromotions.ts           # React Query 查询
│   ├── useCreatePromotion.ts
│   └── ...
├── api.ts                         # API 调用函数（用 services/apiClient）
├── types.ts                       # TypeScript 类型
└── index.ts                       # 导出公开 API
```

| Feature | 覆盖故事 | 单元归属 |
|---|---|---|
| auth | EP01 | U01, U09 |
| product | EP02 | U02, U10b, U17 |
| design | EP03 | U10a |
| blogger | EP04 | U03, U11 |
| promotion | EP05 | U04 |
| finance | EP06 | U05, U16 |
| importer | EP07 | U06a-e, U12, U13 |
| wecom | EP08 | U07, U15 |
| report | EP09 | U08, U14, U17 |

---

## 7. 组件总数小结

| 类别 | 数量 |
|---|---|
| 横切核心组件（core/） | 11（config, db, cache, celery, security/4 子项, tenancy, audit, attachment, state_machine） |
| 业务模块（modules/） | 10 个 Epic 包，约 35 个 Service/Domain/Repository |
| 跨模块服务（services/metric/） | 1 个 MetricService（含 7 个子文件） |
| 前端 features | 10 个 |
| 前端共用组件 | 约 8 个（AppLayout、DateRangePicker、PermissionGate、AttachmentUpload 等） |
| **总计** | **约 75 个一级组件 / 文件** |
