# 服装电商运营管理系统 — 需求规格说明书

## 意图分析

| 属性 | 值 |
|------|------|
| 用户请求 | 基于开发文档构建服装电商运营管理系统（全栈：React 前端 + FastAPI 后端） |
| 请求类型 | 新项目（New Project） |
| 范围估计 | 系统级（System-wide）— 全栈开发，涵盖前后端、数据库、缓存、任务队列、文件存储、外部集成 |
| 复杂度估计 | 复杂（Complex）— 多模块、多角色、多数据源、多外部系统集成 |
| 需求深度 | 综合（Comprehensive）— 文档已提供详尽设计，用户要求全功能交付 |
| 交付方式 | **分阶段交付**（详见第10节）— 最终目标覆盖 P0–P3，但按 MVP→V1→V2→P3 阶段验收，避免一次性混批生成 |

---

## 1. 项目概述

### 1.1 系统定位
一站式服装电商运营管理平台，整合多源数据（千牛、万相台、灰豚），提供智能录入、可视化看板、企微自动化推送，解决数据分散、录入低效、报表失真、决策靠经验等核心痛点。

### 1.2 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + TypeScript + Ant Design 5 + Vite |
| 后端 | Python FastAPI + SQLAlchemy 2.0 (async) |
| 数据库 | PostgreSQL 16 |
| 缓存 | Redis 7 |
| 任务队列 | Celery + Redis |
| 文件存储 | Cloudflare R2 + CDN |
| 部署 | Zeabur（香港节点）+ Docker + GitHub |
| AI | DeepSeek V4（P3阶段） |

### 1.3 交付范围
最终目标覆盖全部功能（P0 + P1 + P2 + P3），但**按阶段验收，不一次性混批生成**：
- **MVP/P0**：商品成本表、站外推广、催发系统+企微基础能力、财务结款、发文进度表、统一导入（Excel/CSV）
- **V1/P1**：设计制版全流程、工作进度表、博主库+智能标签、千牛/万相台/灰豚 RPA 采集、投产报表、店铺数据、字段级权限
- **V2/P2**：拍单、刷单、余额核对、BI看板、Excel导出增强
- **P3**：AI决策建议（DeepSeek V4），作为独立实验功能，不阻塞 MVP 上线

每个阶段必须包含：API + 数据库迁移 + 核心测试 + 部署配置 + 可运行验收。详见**第10节 阶段性交付与验收边界**。

---

## 2. 功能需求

### 2.1 设计制版管理（P1）

| 功能 | 说明 |
|------|------|
| 设计管理 | 新增设计款、面辅料填写上传、面辅料补齐、转大货 |
| 制版管理 | 上传制版文件、填写版号、放码 |
| 工艺管理 | 上传工艺信息 |
| 核价管理 | 核价信息填写、填写吊牌价、价格确认 |
| 状态流转 | 设计中→制版中→工艺录入→待补全→待核价→已确认→大货，支持驳回和取消 |
| 角色协作 | 设计师、版师、跟单、设计助理各司其职，状态推进自动通知下一角色 |

### 2.2 数据管理

| 功能 | 说明 |
|------|------|
| 商品成本表 | SKU级明细，款式编码+商品编码管理，成本价联动下游 |
| 博主库 | 1763+博主资产，含灰豚采集数据、系统计算字段（博主类型/质量标签/假号判断） |
| 千牛数据 | 外部导入，商品维度日报，38列字段 |
| 单品站内推广数据 | 外部导入，站内付费推广效果，72列字段 |

### 2.3 推广管理

| 功能 | 说明 |
|------|------|
| 站外推广 | 核心录入表，记录每笔博主合作全生命周期，含催发状态实时计算 |
| 工作进度表 | 按PR统计月度工作进度，支持时间筛选 |
| 爆款约篇数量 | 按款号设置最低约篇目标，统计达标情况 |
| 发文进度表 | 三层看板（全局汇总→商品卡片→详情面板） |

### 2.4 财务管理

| 功能 | 说明 |
|------|------|
| 财务结款 | 佣金结算全流程（自动生成→初查→核查→付款→上传截图） |
| 拍单 | 站外推广"店铺拍单=是"时自动生成，财务付款 |
| 刷单 | 独立管理，金额自动解析，ROI计算时自动剔除 |
| 余额核对 | 余额自动计算，不一致时红色报错 |

### 2.5 报表与分析

| 功能 | 说明 |
|------|------|
| 店铺数据 | 千牛数据按日汇总，支持时间范围筛选 |
| 投产报表 | 按款式维度汇总全链路数据，含退货率/加购成本/净投产比 |
| BI看板 | 可视化仪表盘 |
| 时间筛选 | 所有看板统一支持近7天/30天/本月/上月/自定义 |

### 2.6 系统管理

| 功能 | 说明 |
|------|------|
| 用户管理 | CRUD、启用/禁用、重置密码 |
| 权限配置 | 完整RBAC：预设角色 + 自定义权限 + 字段级权限 |
| 系统设置 | 阈值配置、消息模板、字段映射、平台凭据管理 |

### 2.7 数据采集引擎

| 功能 | 说明 |
|------|------|
| 凭据管理 | AES-256加密存储平台账号密码，按租户独立密钥 |
| 任务调度 | Celery Beat定时触发采集任务 |
| RPA Worker | 后台采集Worker定时登录千牛/万相台/灰豚导出数据 |
| 统一导入 | import_batch → import_job → field_mapping → 校验 → 入库 |
| 失败处理 | 失败明细下载、单条/批量重试、指数退避 |
| 字段映射版本 | 平台格式变化时新增版本 |
| 幂等机制 | file_hash(SHA256)去重 |

### 2.8 企业微信集成

| 功能 | 说明 |
|------|------|
| 催发消息 | 通过企微群发助手API发送催发消息给博主 |
| 回调处理 | 接收企微回调通知，更新消息状态 |
| 降级策略 | 频控触发时自动切换为通知PR手动催发 |
| 发文通知 | PR填入发布链接后通知控评 |
| 异常预警 | 退货率/转化率/投产比异常时推送管理群 |
| 消息模板 | 可自由编辑，支持变量替换 |

### 2.9 AI决策建议（P3）

| 功能 | 说明 |
|------|------|
| DeepSeek V4集成 | 基于数据分析提供运营决策建议 |
| 应用场景 | 博主选择建议、投放策略优化、异常原因分析 |

---

## 3. 非功能需求

### 3.1 性能

| 指标 | 要求 |
|------|------|
| API响应 | ≤500ms (P95) |
| 页面加载 | ≤3秒 |
| 并发用户 | ≥50人 |

### 3.2 可用性与可靠性

| 指标 | 要求 |
|------|------|
| 可用性 | ≥99.5% |
| RPO | ≤24小时 |
| RTO | ≤4小时 |
| 数据备份 | 每日自动(pg_dump→R2)，30天每日+每月1份保留1年 |
| 恢复演练 | 每季度一次 |

### 3.3 安全

| 指标 | 要求 |
|------|------|
| 认证 | JWT + bcrypt |
| 授权 | 完整RBAC + 字段级权限 + PostgreSQL RLS |
| 传输 | HTTPS |
| API限流 | slowapi库 |
| CORS | 仅允许 app.clothinkai.com |
| 凭据存储 | AES-256加密，密钥与数据库分离 |
| 审计 | 所有关键操作记录结构化JSON日志 |

### 3.4 多租户

| 指标 | 要求 |
|------|------|
| 隔离方式 | 共享数据库 + tenant_id + PostgreSQL RLS |
| 唯一约束 | 所有业务唯一键带 tenant_id |
| 文件隔离 | R2按租户路径隔离 |
| 资源限制 | 每租户可配置存储上限、用户数上限 |

### 3.5 可维护性

| 指标 | 要求 |
|------|------|
| 数据库迁移 | Alembic管理schema变更 |
| 日志 | 结构化JSON日志 |
| 监控 | API响应时间、错误率、数据库连接数 |
| 灰度/回滚 | Zeabur多版本部署，流量切换 |

### 3.6 测试

| 指标 | 要求 |
|------|------|
| 单元测试 | 覆盖核心业务逻辑 |
| 集成测试 | 覆盖API端点和数据库交互 |
| API测试 | 覆盖所有端点的正常和异常场景 |

---

## 4. 数据需求

### 4.1 final.xlsx 定位（不是完整历史数据）
final.xlsx 在本项目中的定位是**字段结构 + 样例种子数据 + 导入流程验证**，**不承诺**作为完整历史数据迁移：

| 用途 | 说明 |
|------|------|
| 字段结构参考 | 各 Sheet 的列定义作为系统字段设计依据，配合 `指标字典.md` 确定字段血缘 |
| 样例种子数据 | 选取少量代表性记录（如 ~50 条商品、~100 条博主、~200 条推广）作为开发/测试种子 |
| 导入流程验证 | 用真实 Sheet 验证统一导入链路（import_batch → field_mapping → 校验 → 入库）是否能正确处理 WPS DISPIMG、万单位、日期序列号、合并单元格等特殊格式 |

**不做**的事情：
- ❌ 按 Excel 当前行数（如 12207 行千牛数据）写一次性迁移脚本
- ❌ 以 Excel 当前空值/异常状态作为系统约束
- ❌ 把 Excel 当成生产数据库的初始快照

历史数据如何进入系统：用户后续通过统一导入入口（手动上传或 RPA 采集）按需补录。

### 4.2 数据模型
按开发文档第十三节定义。详细约束见**第11节 数据模型核心约束**。

核心实体清单：
- tenant、user、role、permission
- style、sku、platform_product、bundle_product
- blogger、wecom_contact
- promotion、settlement、order_adjustment、balance_record
- import_batch、import_job、field_mapping、data_quality_issue
- qianniu_daily、ad_daily
- audit_log、notification、wecom_message、attachment

### 4.3 数据质量
- 导入和业务流转时校验数据质量
- 异常记录写入 data_quality_issue 表
- 支持 info/warning/error 三级严重度
- error 级别阻断业务流转（如禁止提交财务），warning 级别提示但允许继续，info 级别仅记录

---

## 5. 外部集成

| 系统 | 集成方式 | 说明 |
|------|----------|------|
| 千牛 | RPA Worker采集 | 商品日报数据自动同步 |
| 万相台 | RPA Worker采集 | 站内推广数据自动同步 |
| 灰豚 | RPA Worker采集 | 博主画像数据自动同步 |
| 企业微信 | REST API | 催发消息、群发助手、回调通知 |
| Cloudflare R2 | S3兼容API | 图片存储、备份存储 |
| DeepSeek V4 | API调用 | AI决策建议（P3） |

---

## 6. 部署需求

### 6.1 部署架构

| 项目 | 说明 |
|------|------|
| 代码托管 | GitHub |
| 部署平台 | Zeabur（香港节点），从GitHub导入部署 |
| 本地开发 | Docker Compose（PostgreSQL + Redis + Backend + Celery） |
| 域名 | app.clothinkai.com（前端）、api.clothinkai.com（后端） |
| CI/CD | GitHub → Zeabur自动部署 |

### 6.2 Zeabur 服务拆分

部署到 Zeabur 时必须拆分为以下独立服务，**禁止**把 Celery 和 API 混进同一进程：

| 服务名 | 类型 | 说明 |
|------|------|------|
| `frontend` | Web 服务 | React 静态资源，绑定 app.clothinkai.com |
| `backend` | Web 服务 | FastAPI（uvicorn），绑定 api.clothinkai.com |
| `celery-worker` | Worker 服务 | 处理异步任务（导入、催发、采集、企微发送） |
| `celery-beat` | 定时任务 | 触发定时任务（采集调度、催发扫描、备份） |
| `postgres` | Zeabur 内置插件 | PostgreSQL 16 |
| `redis` | Zeabur 内置插件 | Redis 7（缓存 + Celery broker/backend） |

### 6.3 RPA Worker 部署说明
RPA Worker 涉及浏览器自动化，可能不适合直接跑在 Zeabur 容器。本项目要求：
- RPA Worker **可独立部署**（如自建 VM 或 Docker 主机），通过 Celery 队列与主系统解耦
- 主系统通过统一导入 API 接收 RPA 上传的数据，不假设 RPA 与 backend 在同一节点

---

## 7. 用户角色

| 角色 | 核心职责 |
|------|----------|
| 管理员 | 全模块访问、权限配置、数据回滚 |
| 设计师 | 设计管理（新增设计款、面辅料） |
| 设计助理 | 面辅料补齐、核价信息填写、转大货 |
| 版师 | 制版文件、版号、放码 |
| 跟单 | 商品成本表、工艺管理、核价审批 |
| PR | 博主库、站外推广录入 |
| PR主管 | PR全部 + 财务结款核查 |
| 财务 | 付款、拍单、刷单、余额核对 |
| 运营 | 店铺数据、千牛数据、投产报表、BI看板（只读） |

---

## 8. 业务规则配置化

所有阈值均在系统设置中可配置：

| 规则 | 默认值 |
|------|--------|
| 爆文标记阈值 | 点赞≥1000 |
| 爆文统计阈值 | 点赞≥500 |
| 抖音折算系数 | ÷10 |
| 假号判断阈值 | 阅读点赞比≤0.1 |
| 催发天数 | ≤10天 |
| 重要催发天数 | ≤3天 |
| 退货率预警 | >40% |
| 转化率骤降 | 日环比下降>30% |

---

## 9. 约束与假设

### 约束
- 部署在Zeabur香港节点，无需备案
- 企微群发每博主每天最多1条，每PR每天最多1次
- 数据采集引擎的RPA实现细节不暴露给用户
- 敏感图片使用私有桶+签名URL

### 假设
- 用户已有企微自建应用的配置信息
- 用户已有千牛/万相台/灰豚的账号
- final.xlsx 仅用作字段结构参考和样例种子数据（详见 4.1）
- 首次部署即支持多租户，但初始只有一个租户

---

## 10. 阶段性交付与验收边界

本项目最终目标覆盖 P0–P3，但开发**按阶段验收**，避免一次性混批生成。

### 10.1 MVP / P0
- 认证、RBAC（预设角色 + 模块/功能级权限）
- style / sku / blogger / promotion 核心 CRUD
- 统一导入入口（Excel/CSV 手动上传），import_batch / import_job / field_mapping / data_quality_issue
- 财务结款核心流程（自动生成 → 核查 → 付款 → 截图）
- 发文进度核心报表（全局汇总 + 商品卡片）
- 企微催发基础能力（消息模板、群发 API、频控降级）

### 10.2 V1 / P1
- 设计制版全流程（设计 → 制版 → 工艺 → 核价）+ 状态机
- 工作进度表、爆款约篇数量
- 千牛 / 万相台 / 灰豚 RPA 自动采集（凭据加密、调度、审计）
- 投产报表、店铺数据
- 完整字段级权限（如成本价、佣金、付款金额对应敏感字段）
- 异常预警（退货率、转化率、净投产比）

### 10.3 V2 / P2
- 拍单、刷单、余额核对
- BI 看板可视化
- Excel 导出增强（按模块导出、含衍生字段）
- 套装/组合商品（bundle_product）

### 10.4 P3
- AI 决策建议（DeepSeek V4 集成）
- **独立实验功能**，不阻塞 MVP / V1 / V2 上线
- 不依赖 P3 的功能必须在前序阶段已稳定

### 10.5 每阶段最低验收门槛
每个阶段必须**全部满足**才能进入下一阶段：

| 项 | 要求 |
|------|------|
| API | 阶段内功能的 API 端点全部实现并通过集成测试 |
| 数据库迁移 | Alembic 脚本可正向升级、可回滚 |
| 测试 | 单元测试覆盖核心业务逻辑，集成测试覆盖 API 端点 |
| 部署 | 可在本地 Docker Compose 启动，可在 Zeabur 部署成功 |
| 验收 | 用户按本阶段验收标准（详见第13节）实测通过 |

---

## 11. 数据模型核心约束

以下是必须在数据库 schema 和 ORM 层强制保证的约束（不依赖外部文档）：

### 11.1 多租户唯一性
- 所有业务表必须包含 `tenant_id`（FK → tenant.id）
- 所有"业务唯一键"必须带 tenant_id 作为复合唯一约束的一部分：
  - `style`：UNIQUE (tenant_id, style_code)
  - `sku`：UNIQUE (tenant_id, sku_code)
  - `blogger`：UNIQUE (tenant_id, xiaohongshu_id)
  - `promotion`：UNIQUE (tenant_id, internal_code)
  - `settlement`：UNIQUE (tenant_id, settlement_no)
  - `order_adjustment`：UNIQUE (tenant_id, order_no)
  - `import_batch`：UNIQUE (tenant_id, file_hash)
  - `qianniu_daily`：UNIQUE (tenant_id, platform_product_id, date)
  - `ad_daily`：UNIQUE (tenant_id, platform_product_id, date)
- ORM 层自动在所有查询附加 `tenant_id` 条件
- 核心表启用 PostgreSQL RLS

### 11.2 款式与 SKU 关系
- `style.style_code` 在租户内唯一
- `sku.sku_code` 在租户内唯一
- `sku.style_id` 是必填外键（FK → style.id），即一个 SKU 必属于一个款式
- 同一 style 下可有多个 sku（颜色 × 尺码）
- 成本/采购/基本售/吊牌价归属 `sku` 表
- 设计状态、品牌、品类、商品标签、主图归属 `style` 表

### 11.3 推广合作关联
- `promotion.style_id` 必填（FK → style.id）
- `promotion.sku_id` 可选（FK → sku.id），只有当推广精确到具体颜色/尺码时填写
- `promotion.blogger_id` 必填（FK → blogger.id）
- 状态字段拆分：`publish_status` / `recall_status` / `settlement_status` 各为独立字段
- `urge_status`（催发状态）**实时计算**，不存库

### 11.4 平台商品映射
- `platform_product`：UNIQUE (tenant_id, platform, platform_id)
- 一个 platform_product 可关联到 style 或 sku（至少一个非空）
- 千牛日报、站内推广日报通过 `platform_product_id` 关联到业务表

### 11.5 软删除与审计
所有业务表统一字段：
- `id` UUID 主键
- `tenant_id` 租户 FK
- `created_at` / `updated_at` UTC 时间戳
- `deleted_at` 软删除时间（NULL 表示未删除）
- `created_by` / `updated_by` 操作人（FK → user.id，可空）

金额字段统一 `DECIMAL(12,2)`。

### 11.6 订单类型统一建模
拍单和刷单共用 `order_adjustment` 表：
- `order_type` ∈ {拍单, 刷单}
- 刷单记录默认 `exclude_from_roi = true`
- 投产报表计算真实 ROI 时必须排除 `exclude_from_roi=true` 的订单

---

## 12. 平台凭据与采集授权要求（高敏感）

平台账号密码属于高敏感数据，本节验收条件**必须全部满足**才能交付 RPA 采集功能。

### 12.1 用户授权流程
- 用户主动在系统中**新增凭据**才能启用对应平台采集
- 新增凭据时必须显示隐私提示和数据用途说明，用户**主动确认**后才能保存
- 默认禁用，需用户**显式开启**采集任务

### 12.2 加密存储
- 密码使用 AES-256 加密存储
- 加密密钥与数据库分离（存环境变量或 KMS）
- 按租户独立加密密钥，租户间凭据互不可见
- P1 阶段引入 KMS 托管密钥 + 90 天自动轮换

### 12.3 不可回显
- 任何 API 响应、日志、错误信息**不得返回明文密码**
- 界面上密码输入框只能写入，不能读取
- 已存储凭据的列表只显示账号、平台、状态，不显示密码字段

### 12.4 解密审计
- 采集 Worker 每次解密凭据必须写 `audit_log`
- 审计字段：tenant_id、user_id（如人工触发）、credential_id、platform、operation=decrypt、timestamp、purpose
- 审计日志只能新增、不能修改/删除（append-only）

### 12.5 暂停与删除
- 用户可随时**暂停**指定平台的采集（任务跳过该凭据）
- 用户可随时**删除**凭据，删除后立即从存储清除明文，相关采集任务自动停止
- 暂停和删除操作必须写审计日志

### 12.6 采集失败告警
- 凭据失效（登录失败、验证码、IP 风控等）必须触发告警
- 告警通过企微推送给租户管理员
- 连续 N 次失败（默认 3 次）后自动暂停该凭据，避免账号锁定

### 12.7 最小权限建议
- 引导用户使用平台子账号（只读权限）配置采集
- 在隐私协议中明确告知用户数据存储方式

---

## 13. 验收标准（Given / When / Then）

P0 模块的验收标准（部分关键场景），开发完成时必须**全部通过**。

### 13.1 统一导入

**Given** 用户在系统中上传一个有效的千牛 CSV 文件  
**When** 调用 `POST /api/import/upload` 接口  
**Then** 返回 200，包含 `batch_id`、`imported`、`failed` 字段；import_batch 状态为 `completed` 或 `partial_failed`；成功行写入 qianniu_daily；失败行写入 import_job 并附 `error_detail`

**Given** 用户上传与已有批次相同 hash 的文件  
**When** 调用上传接口  
**Then** 返回去重提示，不重复创建 import_batch

**Given** 上传失败的批次  
**When** 调用 `POST /api/import/batches/{id}/retry`  
**Then** 仅重试失败的 import_job 行，成功行不重复处理

### 13.2 推广创建与催发状态

**Given** PR 创建一条推广（cooperation_date = 今天，scheduled_publish_date = 今天 + 15 天）  
**When** 查询 `GET /api/promotions/{id}`  
**Then** 返回的 `urge_status` = `档期内`

**Given** 同一条推广，scheduled_publish_date 改为今天 + 5 天  
**When** 再次查询  
**Then** `urge_status` = `催发`

**Given** scheduled_publish_date = 今天 + 1 天  
**When** 查询  
**Then** `urge_status` = `重要催发`

**Given** scheduled_publish_date = 昨天，publish_status = 未发布  
**When** 查询  
**Then** `urge_status` = `超时`

### 13.3 结算生成

**Given** 一条推广 publish_status 从未发布变为已发布  
**When** PR 主管审核通过  
**Then** 自动生成一条 settlement，settlement_status = `待付款`，金额按推广报价填入

**Given** 一条 settlement 状态为 `待付款`，缺失付款金额、日期或截图任一字段  
**When** 调用付款标记接口  
**Then** 返回 422 校验失败，data_quality_issue 写入 error 级别记录

### 13.4 企微频控降级

**Given** 某博主当天已收到 1 条企微群发  
**When** 系统再次尝试给该博主发催发消息  
**Then** wecom_message 状态置为 `rate_limited`；同时给对应 PR 推送站内通知"请手动催发"

**Given** 某 PR 当天已发起 1 次群发  
**When** 系统再次尝试以该 PR 名义发起群发  
**Then** 本批降级为站内通知，记录 rate_limited

### 13.5 权限拒绝

**Given** 用户角色 = 设计师  
**When** 访问 `GET /api/settlements/`  
**Then** 返回 403，错误响应符合统一错误码格式 `{ code, message, details }`

**Given** 用户没有"成本价"字段读权限  
**When** 调用 `GET /api/skus/{id}`  
**Then** 响应中 `cost_price` 字段被屏蔽（返回 null 或字段不出现）

### 13.6 报表指标

**Given** 一个月内有 10 条推广，其中 6 条已发布、4 条爆文（≥1000 赞）  
**When** 调用 `GET /api/reports/progress/summary?month=...`  
**Then** 发布量 = 6，爆文数 = 4，爆文率 = 4/6 ≈ 0.667

**Given** 推广总成本 1000 元，有效点赞量 0  
**When** 计算 CPL  
**Then** 返回 null，前端显示为 `—`，不抛错

**Given** 站内+站外总花费 0  
**When** 计算净投产比  
**Then** 返回 null，前端显示为 `—`

### 13.7 凭据安全

**Given** 用户配置千牛凭据  
**When** 调用 `GET /api/settings/credentials/{id}`  
**Then** 响应不包含明文密码字段，仅返回账号、平台、状态、updated_at

**Given** RPA Worker 执行采集任务  
**When** 解密凭据  
**Then** audit_log 写入一条 `operation=decrypt` 记录，包含 tenant_id、credential_id、purpose、timestamp

