# 用户故事（User Stories）

## 文档规范

- **故事 ID**：`<EpicCode>-S<NN>`，如 `EP05-S03`
- **故事格式**：标题 + 表格（角色 / 动作 / 价值 / 阶段 / 需求关联 / Journey）+ Given/When/Then 验收标准
- **阶段标签**：MVP（P0） / V1（P1） / V2（P2） / P3
- **需求关联**：指向 `aidlc-docs/inception/requirements/requirements.md` 的章节号
- **Journey 标签**：跨 Epic 的端到端流程标识，可选

## Journey 清单

| Journey ID | 名称 | 涉及 Epic |
|---|---|---|
| J1 | 设计到大货 | EP03 → EP02 |
| J2 | 推广全生命周期 | EP05 → EP08 → EP06 |
| J3 | 数据采集到报表 | EP07 → EP09 |
| J4 | 财务结款闭环 | EP05 → EP06 |

## Epic 清单

| Epic | 名称 | 默认阶段 | 主要角色 |
|---|---|---|---|
| EP01 | 认证与权限 | MVP | 管理员 |
| EP02 | 商品与 SKU 管理 | MVP/V2 | 跟单、管理员 |
| EP03 | 设计制版全流程 | V1 | 设计师、设计助理、版师、跟单 |
| EP04 | 博主库与智能标签 | MVP/V1 | PR、PR 主管 |
| EP05 | 推广合作生命周期 | MVP | PR、PR 主管 |
| EP06 | 财务结款 / 拍单 / 刷单 / 余额 | MVP/V2 | PR 主管、财务 |
| EP07 | 数据采集与统一导入 | MVP/V1 | 管理员、PR、运营 |
| EP08 | 企业微信集成 | MVP/V1 | PR、管理员 |
| EP09 | 报表与看板 | MVP/V1 | PR、PR 主管、运营 |
| EP10 | NFR（非功能需求 Checklist） | 全阶段 | 管理员 |
| EP11 | AI 决策建议 | P3 | 运营、PR 主管 |

## 故事清单总览

故事总数：**104 个**，分为三类，便于后续 AI-DLC 按类型处理。

### 可实施故事（Implementable Stories，89 个）

> 可在 Construction 阶段拆解为 API/页面/任务实现单元的故事。

| ID | 标题 | Epic | 角色 | 阶段 | 需求关联 | Journey |
|---|---|---|---|---|---|---|
| EP01-S01 | 用户登录 | EP01 | 全角色 | MVP | 3.3 | — |
| EP01-S02 | 修改密码 | EP01 | 全角色 | MVP | 3.3 | — |
| EP01-S03 | 管理员管理用户 | EP01 | 管理员 | MVP | 7 | — |
| EP01-S04 | 管理员分配预设角色 | EP01 | 管理员 | MVP | 3.3, 7 | — |
| EP01-S05 | 管理员配置自定义权限 | EP01 | 管理员 | V1 | 3.3 | — |
| EP01-S06 | 字段级权限控制 | EP01 | 管理员、财务、跟单 | V1 | 3.3, 13.5 | — |
| EP01-S07 | 多租户隔离 | EP01 | 管理员 | MVP | 3.4, 11.1 | — |
| EP01-S08 | 审计日志查询 | EP01 | 管理员 | MVP | 3.5, 12.4 | — |
| EP02-S01 | 跟单创建款式 | EP02 | 跟单 | MVP | 2.2, 11.2 | J1 |
| EP02-S02 | 跟单创建 SKU | EP02 | 跟单 | MVP | 2.2, 11.2 | J1 |
| EP02-S03 | 编辑款式信息 | EP02 | 跟单 | MVP | 2.2 | — |
| EP02-S04 | 编辑 SKU 成本/价格 | EP02 | 跟单 | MVP | 2.2 | — |
| EP02-S05 | 按款式查询 SKU | EP02 | 跟单、PR | MVP | 2.2 | — |
| EP02-S06 | 款号↔商品简称双向关联 | EP02 | PR | MVP | 2.3 | J2 |
| EP02-S07 | 平台商品映射 | EP02 | 管理员、跟单 | V1 | 2.2, 11.4 | — |
| EP02-S08 | 套装/组合商品 | EP02 | 跟单 | V2 | 2.2, 4.2 | — |
| EP03-S02 | 设计师上传设计稿 | EP03 | 设计师 | V1 | 2.1 | J1 |
| EP03-S03 | 设计师填写面辅料 | EP03 | 设计师 | V1 | 2.1 | J1 |
| EP03-S04 | 版师提交版型与版号 | EP03 | 版师 | V1 | 2.1 | J1 |
| EP03-S05 | 版师放码 | EP03 | 版师 | V1 | 2.1 | J1 |
| EP03-S06 | 版师驳回到设计中 | EP03 | 版师 | V1 | 2.1 | J1 |
| EP03-S07 | 跟单录入工艺信息 | EP03 | 跟单 | V1 | 2.1 | J1 |
| EP03-S08 | 设计助理面辅料补齐 | EP03 | 设计助理 | V1 | 2.1 | J1 |
| EP03-S09 | 设计助理填写核价信息 | EP03 | 设计助理 | V1 | 2.1 | J1 |
| EP03-S10 | 跟单填写吊牌价 | EP03 | 跟单 | V1 | 2.1 | J1 |
| EP03-S11 | 跟单价格确认转大货 | EP03 | 跟单 | V1 | 2.1 | J1 |
| EP03-S12 | 任意环节驳回 | EP03 | 跟单、设计助理、版师 | V1 | 2.1 | J1 |
| EP03-S13 | 管理员取消款式 | EP03 | 管理员 | V1 | 2.1 | J1 |
| EP03-S14 | 状态推进自动通知 | EP03 | 全设计制版角色 | V1 | 2.1 | J1 |
| EP04-S01 | PR 添加博主 | EP04 | PR | MVP | 2.2 | — |
| EP04-S02 | PR 编辑博主信息 | EP04 | PR | MVP | 2.2 | — |
| EP04-S03 | 博主搜索与筛选 | EP04 | PR、PR 主管 | MVP | 2.2 | — |
| EP04-S04 | 系统计算博主类型 | EP04 | PR | V1 | 2.2, 8 | — |
| EP04-S05 | 系统计算阅读点赞比 | EP04 | PR | V1 | 2.2, 8 | — |
| EP04-S06 | 假号判断 | EP04 | PR | V1 | 2.2, 8 | — |
| EP04-S07 | 质量标签 | EP04 | PR | V1 | 2.2, 8 | — |
| EP04-S08 | 灰豚画像数据展示 | EP04 | PR | V1 | 2.2, 2.7 | J3 |
| EP05-S02 | PR 创建推广合作 | EP05 | PR | MVP | 2.3, 13.2 | J2 |
| EP05-S03 | 自动按款号填充商品简称 | EP05 | PR | MVP | 2.3 | J2 |
| EP05-S04 | 同款博主重复检测 | EP05 | PR | MVP | 2.3 | J2 |
| EP05-S05 | 双平台标记 | EP05 | PR | MVP | 2.3 | J2 |
| EP05-S06 | 实时计算催发状态 | EP05 | PR | MVP | 2.3, 11.3, 13.2 | J2 |
| EP05-S07 | PR 填入发布链接 | EP05 | PR | MVP | 2.3 | J2 |
| EP05-S08 | PR 取消合作 | EP05 | PR | MVP | 2.3 | J2 |
| EP05-S09 | PR 发起召回 | EP05 | PR | MVP | 2.3 | J2 |
| EP05-S10 | 系统计算有效点赞量 | EP05 | PR | MVP | 2.3, 8 | — |
| EP05-S11 | 爆文标记 | EP05 | PR | MVP | 2.3, 8 | — |
| EP05-S12 | 计算单件点赞成本 | EP05 | PR | MVP | 2.3 | — |
| EP05-S13 | PR 主管审核推广 | EP05 | PR 主管 | MVP | 2.3, 13.3 | J2 J4 |
| EP06-S02 | 自动生成结算单 | EP06 | PR 主管 | MVP | 2.4, 13.3 | J4 |
| EP06-S03 | PR 主管核查结算单 | EP06 | PR 主管 | MVP | 2.4 | J4 |
| EP06-S04 | PR 主管驳回结算 | EP06 | PR 主管 | MVP | 2.4 | J4 |
| EP06-S05 | PR 主管增加结算项 | EP06 | PR 主管 | MVP | 2.4 | J4 |
| EP06-S06 | PR 主管填写付款金额 | EP06 | PR 主管 | MVP | 2.4, 13.3 | J4 |
| EP06-S07 | 财务上传付款截图 | EP06 | 财务 | MVP | 2.4, 13.3 | J4 |
| EP06-S08 | 当日结算汇总 | EP06 | PR 主管、财务 | MVP | 2.4 | J4 |
| EP06-S09 | 拍单自动生成 | EP06 | 财务 | V2 | 2.4 | — |
| EP06-S10 | 刷单录入与 ROI 隔离 | EP06 | 财务 | V2 | 2.4, 11.6 | — |
| EP06-S11 | 余额核对 | EP06 | 财务 | V2 | 2.4 | — |
| EP07-S02 | 管理员添加平台凭据 | EP07 | 管理员 | V1 | 12.1, 12.2 | — |
| EP07-S03 | 凭据加密存储且不可回显 | EP07 | 管理员 | V1 | 12.3, 13.7 | — |
| EP07-S04 | 凭据解密审计 | EP07 | 管理员 | V1 | 12.4, 13.7 | — |
| EP07-S05 | 管理员暂停/删除凭据 | EP07 | 管理员 | V1 | 12.5 | — |
| EP07-S06 | 采集失败告警 | EP07 | 管理员 | V1 | 12.6 | — |
| EP07-S07 | 手动上传 Excel/CSV | EP07 | PR、运营 | MVP | 2.7, 13.1 | — |
| EP07-S08 | 文件 hash 去重 | EP07 | PR、运营 | MVP | 2.7, 13.1 | — |
| EP07-S09 | 字段映射版本管理 | EP07 | 管理员 | MVP | 2.7 | — |
| EP07-S10 | 失败行下载与重试 | EP07 | PR、运营 | MVP | 2.7, 13.1 | — |
| EP07-S11 | 自动同步千牛 | EP07 | 管理员 | V1 | 2.7 | J3 |
| EP07-S12 | 自动同步万相台 | EP07 | 管理员 | V1 | 2.7 | J3 |
| EP07-S13 | 自动同步灰豚 | EP07 | 管理员 | V1 | 2.7 | J3 |
| EP07-S14 | 数据质量看板 | EP07 | 管理员 | V1 | 4.3 | — |
| EP08-S02 | 配置企微自建应用 | EP08 | 管理员 | MVP | 2.8 | — |
| EP08-S03 | 博主企微外部联系人绑定 | EP08 | PR | MVP | 2.8 | — |
| EP08-S04 | 编辑催发消息模板 | EP08 | 管理员 | MVP | 2.8 | — |
| EP08-S05 | 自动催发扫描定时任务 | EP08 | 管理员 | MVP | 2.8, 8 | J2 |
| EP08-S06 | 触发催发企微群发 | EP08 | PR | MVP | 2.8, 13.4 | J2 |
| EP08-S07 | 频控降级到站内通知 | EP08 | PR | MVP | 2.8, 13.4 | J2 |
| EP08-S08 | 企微回调更新消息状态 | EP08 | 管理员 | MVP | 2.8 | — |
| EP08-S09 | 发文通知控评 | EP08 | PR | V1 | 2.8 | — |
| EP08-S10 | 异常预警推送管理群 | EP08 | 管理员 | V1 | 2.8 | — |
| EP09-S01 | 发文进度三层看板 | EP09 | PR、PR 主管 | MVP | 2.5, 13.6 | J3 |
| EP09-S02 | 工作进度表 | EP09 | PR 主管 | V1 | 2.5 | — |
| EP09-S03 | 爆款约篇数量 | EP09 | PR 主管 | V1 | 2.5 | — |
| EP09-S04 | 店铺数据看板 | EP09 | 运营 | V1 | 2.5 | — |
| EP09-S05 | 投产报表 | EP09 | 运营 | V1 | 2.5, 13.6 | — |
| EP09-S06 | BI 看板 | EP09 | 运营 | V2 | 2.5 | — |
| EP09-S07 | 时间筛选组件 | EP09 | 全报表角色 | MVP | 2.5 | — |
| EP09-S08 | 报表导出 Excel | EP09 | PR 主管、运营 | V2 | 2.5 | — |

**可实施故事按阶段分布**：MVP = 47、V1 = 36、V2 = 6。

### Overview / 总览故事（5 个，不直接产出代码）

> Epic 总览故事用于团队对齐 Epic 的整体范围，不作为独立实现单元。其验收标准会被该 Epic 下"可实施故事"的验收标准之并集覆盖。

| ID | 标题 | Epic | 阶段 | 备注 |
|---|---|---|---|---|
| EP03-S01 | 设计制版总览 | EP03 | V1 | 状态机总览，覆盖 EP03-S02~S14 |
| EP05-S01 | 推广合作 Epic 总览 | EP05 | MVP | 覆盖 EP05-S02~S13 |
| EP06-S01 | 财务结款 Epic 总览 | EP06 | MVP | 覆盖 EP06-S02~S11 |
| EP07-S01 | 数据采集 Epic 总览 | EP07 | MVP | 覆盖 EP07-S02~S14 |
| EP08-S01 | 企微集成 Epic 总览 | EP08 | MVP | 覆盖 EP08-S02~S10 |

### NFR Checklist（7 条，留待 Construction 阶段 NFR Requirements/Design）

> 按规划决策 Q7=C，NFR 不展开成 INVEST 故事，仅以条目占位。

| ID | 标题 | 阶段 | 需求关联 |
|---|---|---|---|
| EP10-NFR01 | 性能 NFR | 全阶段 | 3.1 |
| EP10-NFR02 | 安全 NFR | 全阶段 | 3.3 |
| EP10-NFR03 | 多租户隔离 NFR | MVP | 3.4, 11.1 |
| EP10-NFR04 | 备份与恢复 NFR | MVP | 3.2 |
| EP10-NFR05 | 测试覆盖 NFR | 全阶段 | 3.6, 10.5 |
| EP10-NFR06 | 监控与告警 NFR | V1 | 3.5 |
| EP10-NFR07 | Zeabur 服务拆分 NFR | MVP | 6.2 |

### P3 占位故事（3 个）

> 实验性功能，独立交付，不阻塞 MVP/V1/V2 上线。验收标准为粗粒度（待 P3 阶段细化）。

| ID | 标题 | Epic | 角色 | 需求关联 |
|---|---|---|---|---|
| EP11-S01 | AI 推广策略建议 | EP11 | PR 主管 | 2.9 |
| EP11-S02 | AI 异常原因分析 | EP11 | 运营 | 2.9 |
| EP11-S03 | AI 博主选择建议 | EP11 | PR | 2.9 |

### 总数小结

| 类别 | 数量 |
|---|---|
| 可实施故事 | 89 |
| Overview 总览故事 | 5 |
| NFR Checklist 条目 | 7 |
| P3 占位故事 | 3 |
| **合计** | **104 项**（其中可实施 89） |

> 注：可实施故事 89 略高于早期 60-80 估算，因为系统覆盖 9 大业务模块且每个模块按"INVEST 小颗粒"拆分。后续 Construction 阶段会按 MVP/V1/V2 分批执行，避免一次性混批生成。

---

## EP01：认证与权限

业务目标：保证只有授权用户能访问对应模块，敏感字段按角色屏蔽，所有关键操作可审计。

### EP01-S01 用户登录
| 字段 | 值 |
|---|---|
| As a | 全角色用户 |
| I want | 用账号密码登录系统并获得 JWT |
| So that | 我能访问被授权的模块 |
| 阶段 | MVP | 
| 需求 | 3.3 |

**验收标准**

- **Given** 用户提供有效用户名和密码  
  **When** 调用 `POST /api/auth/login`  
  **Then** 返回 200，含 access_token（JWT）和 refresh_token，token payload 含 user_id / tenant_id / roles
- **Given** 用户提供错误密码  
  **When** 调用登录接口  
  **Then** 返回 401，错误响应符合 `{ code, message }` 格式，audit_log 记录登录失败
- **Given** 同一用户连续 5 次登录失败  
  **When** 第 6 次登录  
  **Then** 返回 429 限流错误

### EP01-S02 修改密码
| As a | 已登录用户 |
|---|---|
| I want | 修改自己的密码 |
| So that | 在首次登录或定期轮换时确保账号安全 |
| 阶段 | MVP | 需求 | 3.3 |

**验收标准**

- **Given** 用户已登录且提供原密码和新密码  
  **When** 调用 `PUT /api/auth/password`  
  **Then** 返回 200，密码以 bcrypt 重新哈希存储，旧 token 立即失效
- **Given** 管理员账号首次登录  
  **When** 用初始随机密码登录  
  **Then** 系统强制跳转到修改密码页面，未修改前其他 API 返回 423（Locked）

### EP01-S03 管理员管理用户
| As a | 管理员 |
|---|---|
| I want | 创建、编辑、启用/禁用用户 |
| So that | 团队成员变动时能及时调整账号 |
| 阶段 | MVP | 需求 | 7 |

**验收标准**

- **Given** 管理员已登录  
  **When** 调用 `POST /api/users/`  
  **Then** 创建用户，返回初始随机密码（明文一次性返回，不存入日志）
- **Given** 用户已启用  
  **When** 管理员调用 `PUT /api/users/{id}/toggle`  
  **Then** 用户被禁用，所有现有 token 立即失效，user.status = disabled
- **Given** 非管理员用户  
  **When** 调用用户管理 API  
  **Then** 返回 403

### EP01-S04 管理员分配预设角色
| As a | 管理员 |
|---|---|
| I want | 给用户分配一个或多个预设角色 |
| So that | 用户能按角色访问相应模块 |
| 阶段 | MVP | 需求 | 3.3, 7 |

**验收标准**

- **Given** 管理员已创建用户  
  **When** 调用 `POST /api/users/{id}/roles`，body 含 role_codes 列表  
  **Then** 用户获得对应角色权限的并集
- **Given** 用户同时拥有"设计师"和"设计助理"两个角色（当前阶段兼任场景）  
  **When** 用户访问设计管理或核价管理  
  **Then** 两个模块都可访问，权限取并集

### EP01-S05 管理员配置自定义权限
| As a | 管理员 |
|---|---|
| I want | 为单个用户自定义权限，覆盖角色默认值 |
| So that | 应对特殊职责调整 |
| 阶段 | V1 | 需求 | 3.3 |

**验收标准**

- **Given** 用户拥有"PR"角色（默认无财务权限）  
  **When** 管理员为该用户授予"提交财务付款"自定义权限  
  **Then** 该用户的有效权限 = 角色默认 ∪ 自定义授予 - 自定义撤销
- **Given** 自定义权限矩阵：行=模块，列=用户，单元格=允许/禁止  
  **When** 调用 `GET /api/users/{id}/effective-permissions`  
  **Then** 返回最终生效的权限列表（含字段级）

### EP01-S06 字段级权限控制
| As a | 跟单 / 财务 / PR 等敏感字段相关角色 |
|---|---|
| I want | 系统按字段级权限屏蔽我无权访问的字段 |
| So that | 商业敏感数据（成本价、佣金、博主报价）不会越权泄露 |
| 阶段 | V1 | 需求 | 3.3, 13.5 |

**验收标准**

- **Given** 用户角色 = 设计师，无 cost_price 读权限  
  **When** 调用 `GET /api/skus/{id}`  
  **Then** 响应中不出现 cost_price 字段（或值为 null）
- **Given** 用户有 cost_price 读权限但无写权限  
  **When** 调用 `PUT /api/skus/{id}` 修改 cost_price  
  **Then** 返回 403，原值不变

### EP01-S07 多租户隔离
| As a | 管理员 |
|---|---|
| I want | 数据按 tenant_id 严格隔离 |
| So that | 不同租户互不可见，符合数据安全要求 |
| 阶段 | MVP | 需求 | 3.4, 11.1 |

**验收标准**

- **Given** 租户 A 创建了 style_code="W001"，租户 B 也尝试创建 style_code="W001"  
  **When** 租户 B 调用 `POST /api/styles/`  
  **Then** 创建成功（因为唯一约束是 tenant_id+style_code）
- **Given** 租户 A 用户登录  
  **When** 调用任意 list API  
  **Then** ORM 自动附加 `tenant_id = A`，返回结果不含租户 B 数据
- **Given** PostgreSQL 直接查询绕过 ORM  
  **When** 应用 RLS 策略  
  **Then** 仍按 current_setting('app.tenant_id') 过滤

### EP01-S08 审计日志查询
| As a | 管理员 |
|---|---|
| I want | 查询所有关键操作的审计日志 |
| So that | 追溯异常、追责、合规检查 |
| 阶段 | MVP | 需求 | 3.5, 12.4 |

**验收标准**

- **Given** 用户做了 CRUD / 权限变更 / 凭据解密 / 数据回滚操作  
  **When** 操作完成  
  **Then** audit_log 写入一条记录，含 tenant_id、user_id、action、resource、timestamp
- **Given** 管理员调用 `GET /api/audit-logs?action=decrypt&date_from=...`  
  **When** 查询  
  **Then** 返回过滤后的日志，按 timestamp DESC 排序，分页
- **Given** 任何用户  
  **When** 尝试 DELETE 或 UPDATE audit_log 记录  
  **Then** 返回 403 或数据库层拒绝（append-only）

---

## EP02：商品与 SKU 管理

业务目标：维护款式、SKU、平台映射、套装作为全系统数据底座。

### EP02-S01 跟单创建款式
| As a | 跟单 |
|---|---|
| I want | 创建款式记录，含基本信息和图片 |
| So that | 后续 SKU、推广、报表都能关联到款式 |
| 阶段 | MVP | 需求 | 2.2, 11.2 | Journey | J1 |

**验收标准**

- **Given** 跟单已登录  
  **When** 调用 `POST /api/styles/`，body 含 style_code、style_name、brand、category 等  
  **Then** 创建成功，返回 style.id（UUID），design_status 默认为"大货"（如果跨过设计制版流程直接录入）或"设计中"（如果走 EP03 流程）
- **Given** 同租户已存在 style_code="W001"  
  **When** 再次创建相同 style_code  
  **Then** 返回 409 冲突错误

### EP02-S02 跟单创建 SKU
| As a | 跟单 |
|---|---|
| I want | 在已有款式下创建 SKU（颜色 × 尺码 × 成本价） |
| So that | 维护商品成本表，下游引用 |
| 阶段 | MVP | 需求 | 2.2, 11.2 | Journey | J1 |

**验收标准**

- **Given** 款式 W001 已存在  
  **When** 调用 `POST /api/skus/` body 含 style_id=W001.id、sku_code="W001-红-M"、color="红"、size="M"、cost_price  
  **Then** 创建成功，sku.style_id 指向 W001
- **Given** 同租户已存在 sku_code="W001-红-M"  
  **When** 再次创建相同 sku_code  
  **Then** 返回 409
- **Given** style_id 不存在  
  **When** 创建 SKU  
  **Then** 返回 422 校验失败

### EP02-S03 编辑款式信息
| As a | 跟单 |
|---|---|
| I want | 编辑款式名称、品牌、品类、标签、主图 |
| So that | 信息变更时及时更新 |
| 阶段 | MVP | 需求 | 2.2 |

**验收标准**

- **Given** 款式存在  
  **When** 调用 `PUT /api/styles/{id}`  
  **Then** 更新成功，updated_at 自动更新，audit_log 记录变更
- **Given** 字段值未变更  
  **When** 调用更新接口  
  **Then** 不写 audit_log（避免噪音）

### EP02-S04 编辑 SKU 成本/价格
| As a | 跟单 |
|---|---|
| I want | 修改 SKU 的成本价、采购价、基本售价 |
| So that | 成本变化能及时反映到投产报表 |
| 阶段 | MVP | 需求 | 2.2 |

**验收标准**

- **Given** 用户角色 = 跟单（有 cost_price 写权限）  
  **When** 调用 `PUT /api/skus/{id}` 修改 cost_price  
  **Then** 更新成功，audit_log 记录新旧值
- **Given** 用户角色 = 设计师（无 cost_price 写权限）  
  **When** 同上调用  
  **Then** 返回 403

### EP02-S05 按款式查询 SKU
| As a | 跟单 / PR |
|---|---|
| I want | 输入款式编码或款式 ID，列出该款式下所有 SKU |
| So that | 推广录入时按颜色尺码精准匹配 |
| 阶段 | MVP | 需求 | 2.2 |

**验收标准**

- **Given** 款式 W001 下有 6 个 SKU  
  **When** 调用 `GET /api/skus/by-style/{style_id}`  
  **Then** 返回 6 个 SKU 记录
- **Given** 款式 W001 没有 SKU  
  **When** 同上调用  
  **Then** 返回空数组，HTTP 200

### EP02-S06 款号↔商品简称双向关联
| As a | PR |
|---|---|
| I want | 输入款号自动填充商品简称，输入商品简称自动填充款号 |
| So that | 录入推广时减少键入工作量，避免错别字 |
| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |

**验收标准**

- **Given** 款式 W001 名为"波点花边长袖"  
  **When** PR 在站外推广录入页输入 style_code="W001"  
  **Then** 商品简称自动填充为"波点花边长袖"
- **Given** PR 输入商品简称"波点花边长袖"  
  **When** 系统按 style_name 模糊匹配  
  **Then** 自动建议候选款号列表，PR 选择后填充 style_code
- **Given** 输入的款号不存在  
  **When** 触发自动填充  
  **Then** 提示"未找到该款号"，不阻塞继续录入

### EP02-S07 平台商品映射
| As a | 管理员 / 跟单 |
|---|---|
| I want | 维护千牛/淘宝商品 ID 与系统款式/SKU 的映射关系 |
| So that | 千牛日报、站内推广日报能关联到内部款式 |
| 阶段 | V1 | 需求 | 2.2, 11.4 |

**验收标准**

- **Given** style W001 + sku "W001-红-M" 已存在  
  **When** 创建 platform_product 记录 (platform="qianniu", platform_id="123456", style_id=W001.id, sku_id=W001-红-M.id)  
  **Then** 创建成功，UNIQUE (tenant_id, platform, platform_id) 生效
- **Given** 同 (tenant_id, platform, platform_id) 已存在  
  **When** 重复创建  
  **Then** 返回 409
- **Given** 千牛日报含 platform_product_id=123456  
  **When** 导入  
  **Then** 自动关联到内部 style/sku

### EP02-S08 套装/组合商品
| As a | 跟单 |
|---|---|
| I want | 创建套装商品（含多个 sku 的组合关系） |
| So that | 套装销售场景下报表能正确归属销量 |
| 阶段 | V2 | 需求 | 2.2, 4.2 |

**验收标准**

- **Given** 已有 sku A 和 sku B  
  **When** 创建 bundle_product，bundle_item 含 (sku_id=A, quantity=1) + (sku_id=B, quantity=1)  
  **Then** bundle 创建成功
- **Given** bundle 销售一件  
  **When** 投产报表计算  
  **Then** 销量按 bundle_item 拆分到各 sku（按数量）

---

## EP03：设计制版全流程

业务目标：固化"设计→制版→工艺→核价→大货"的端到端协作流程，每个环节角色明确、可驳回、状态自动通知下一角色。

### EP03-S01 设计制版总览（Epic 总览故事）
| As a | 设计师 / 设计助理 / 版师 / 跟单 |
|---|---|
| I want | 在统一系统中看到款式当前所处的状态环节和负责人 |
| So that | 协作不再依赖微信通知，每个人都知道自己手上有多少待办 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** 用户登录  
  **When** 进入"设计制版"主页  
  **Then** 看到按状态分组的款式列表（设计中 / 制版中 / 工艺录入 / 待补全 / 待核价 / 大货 / 已取消），每组显示数量
- **Given** 状态机定义（详见需求 2.1 与开发文档第 6.0 节）  
  **When** 任意角色完成自己环节的提交  
  **Then** style.design_status 自动推进，下一角色收到站内通知

### EP03-S02 设计师上传设计稿
| As a | 设计师 |
|---|---|
| I want | 上传设计稿图片创建新款式 |
| So that | 把设计稿正式纳入系统，启动协作流程 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** 设计师登录  
  **When** 调用 `POST /api/designs/`，body 含 style_code、style_name、image  
  **Then** 创建 style 记录，design_status="设计中"，主图存入 R2 public 桶
- **Given** style_code 已存在  
  **When** 创建  
  **Then** 返回 409

### EP03-S03 设计师填写面辅料
| As a | 设计师 |
|---|---|
| I want | 在已创建的款式下补充面料、辅料信息并提交到下一环节 |
| So that | 版师能基于完整面辅料开始制版 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status="设计中"  
  **When** 调用 `PUT /api/designs/{id}/fabric`，body 含 fabrics 列表  
  **Then** 写入 style_fabric 表，design_status 推进到"制版中"，自动通知版师
- **Given** 面辅料字段缺必填项  
  **When** 提交  
  **Then** 返回 422

### EP03-S04 版师提交版型与版号
| As a | 版师 |
|---|---|
| I want | 上传制版文件并填写版号 |
| So that | 跟单可以基于版型录入工艺 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status="制版中"  
  **When** 调用 `PUT /api/designs/{id}/pattern`，body 含 pattern_no、pattern_file  
  **Then** 写入 style_pattern，文件存 R2 private 桶
- **Given** 版号未填  
  **When** 提交  
  **Then** 返回 422

### EP03-S05 版师放码
| As a | 版师 |
|---|---|
| I want | 完成各尺码放码并提交到下一环节 |
| So that | 工艺信息可以基于已放码的版型录入 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** 版型已上传  
  **When** 调用 `PUT /api/designs/{id}/grading`，body 含 grading_data  
  **Then** 更新 style_pattern.grading_data，design_status="工艺录入"，通知跟单

### EP03-S06 版师驳回到设计中
| As a | 版师 |
|---|---|
| I want | 当设计稿不齐全时驳回到设计师重做 |
| So that | 不在版型阶段勉强推进 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status="制版中"  
  **When** 调用 `PUT /api/designs/{id}/reject`，body 含 reason  
  **Then** design_status 回退到"设计中"，driven_by="version_maker"，通知设计师附驳回原因

### EP03-S07 跟单录入工艺信息
| As a | 跟单 |
|---|---|
| I want | 录入生产工艺要求并提交到下一环节 |
| So that | 工厂收到完整工艺单 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status="工艺录入"  
  **When** 调用 `PUT /api/designs/{id}/craft`，body 含 craft_info  
  **Then** 写入 style_craft，design_status="待补全"，通知设计助理

### EP03-S08 设计助理面辅料补齐
| As a | 设计助理 |
|---|---|
| I want | 核查并补全面辅料信息 |
| So that | 核价阶段不会因数据不全卡住 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status="待补全"  
  **When** 调用 `PUT /api/designs/{id}/fabric/complete`，补全字段  
  **Then** 更新 style_fabric，design_status 暂不推进（等核价信息一并提交）

### EP03-S09 设计助理填写核价信息
| As a | 设计助理 |
|---|---|
| I want | 录入成本构成（面料用量、辅料用量、工艺费）并提交核价 |
| So that | 跟单可以基于成本构成定吊牌价 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** 面辅料已补齐  
  **When** 调用 `PUT /api/designs/{id}/complete`，body 含 cost_breakdown  
  **Then** 自动核价：sku.cost_price = 面料用量 + 辅料用量 + 工艺费，design_status="待核价"，通知跟单

### EP03-S10 跟单填写吊牌价
| As a | 跟单 |
|---|---|
| I want | 填写最终吊牌价 |
| So that | 价格策略可控 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status="待核价"  
  **When** 调用 `PUT /api/designs/{id}/tag-price`，body 含 tag_price  
  **Then** 更新 sku.tag_price

### EP03-S11 跟单价格确认转大货
| As a | 跟单 |
|---|---|
| I want | 完成核价审批，确认最终定价并转大货 |
| So that | 款式正式进入推广和销售阶段 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status="待核价"，吊牌价已填  
  **When** 调用 `PUT /api/designs/{id}/confirm-price`  
  **Then** design_status="大货"，通知设计师款式已上线

### EP03-S12 任意环节驳回
| As a | 跟单 / 设计助理 / 版师 |
|---|---|
| I want | 在我的环节发现上游错误时驳回 |
| So that | 错误数据不会向下传递 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** 状态合法（按需求 2.1 可回退表）  
  **When** 调用 `PUT /api/designs/{id}/reject` 含 reason  
  **Then** design_status 回退到上一环节，通知上游附驳回原因，audit_log 记录
- **Given** 状态机不允许从当前状态驳回  
  **When** 调用驳回  
  **Then** 返回 422，提示状态机违规

### EP03-S13 管理员取消款式
| As a | 管理员 |
|---|---|
| I want | 任何状态下取消款式 |
| So that | 不再开发的款式不占系统资源 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** 任意 design_status  
  **When** 管理员调用 `PUT /api/designs/{id}/cancel`，body 含 reason  
  **Then** design_status="已取消"，不可逆，audit_log 记录
- **Given** 已取消的款式  
  **When** 任何角色尝试推进  
  **Then** 返回 422

### EP03-S14 状态推进自动通知
| As a | 设计制版各角色 |
|---|---|
| I want | 我负责的环节有新待办时立即收到通知 |
| So that | 不需要轮询查看，及时响应 |
| 阶段 | V1 | 需求 | 2.1 | Journey | J1 |

**验收标准**

- **Given** style.design_status 从"制版中"变为"工艺录入"  
  **When** 状态变更触发器执行  
  **Then** 给所有"跟单"角色用户写一条 notification（site:in-app）
- **Given** 用户调用 `GET /api/notifications/unread-count`  
  **When** 查询  
  **Then** 返回未读数

---

## EP04：博主库与智能标签

业务目标：把博主资产沉淀到系统中，配合系统计算字段（博主类型、假号判断、质量标签）辅助 PR 选博主决策。

### EP04-S01 PR 添加博主
| As a | PR |
|---|---|
| I want | 录入博主基本信息（小红书 ID、昵称、报价、微信号） |
| So that | 后续推广合作能关联到具体博主 |
| 阶段 | MVP | 需求 | 2.2 |

**验收标准**

- **Given** PR 已登录  
  **When** 调用 `POST /api/bloggers/`  
  **Then** 创建博主，UNIQUE (tenant_id, xiaohongshu_id) 生效
- **Given** 同租户已存在该 xiaohongshu_id  
  **When** 重复创建  
  **Then** 返回 409，提示"该博主已存在，是否查看？"

### EP04-S02 PR 编辑博主信息
| As a | PR |
|---|---|
| I want | 修改报价、备注、合作历史标签 |
| So that | 信息变化时及时更新 |
| 阶段 | MVP | 需求 | 2.2 |

**验收标准**

- **Given** 博主存在  
  **When** 调用 `PUT /api/bloggers/{id}`  
  **Then** 更新成功，audit_log 记录字段变更（报价为敏感字段，必记录）

### EP04-S03 博主搜索与筛选
| As a | PR / PR 主管 |
|---|---|
| I want | 按昵称、ID、博主类型、粉丝量、质量标签筛选博主 |
| So that | 在 1763+ 博主中快速找到目标 |
| 阶段 | MVP | 需求 | 2.2 |

**验收标准**

- **Given** 博主库有数据  
  **When** 调用 `GET /api/bloggers/search?nickname=xxx&blogger_type=KOL&follower_count_min=10000`  
  **Then** 返回符合条件的博主列表，支持分页排序
- **Given** 用户角色 = PR  
  **When** 列表中包含报价字段  
  **Then** 报价字段按字段级权限规则可见或屏蔽

### EP04-S04 系统计算博主类型
| As a | PR |
|---|---|
| I want | 系统按粉丝量自动给博主打类型标签（素人/KOC/KOL） |
| So that | 报表和筛选时基于统一口径 |
| 阶段 | V1 | 需求 | 2.2, 8 |

**验收标准**

- **Given** 博主 follower_count = 5000  
  **When** 系统计算 blogger_type  
  **Then** = 素人（粉丝<1W）
- **Given** follower_count = 50000  
  **When** 系统计算  
  **Then** = KOC（1W-10W）
- **Given** follower_count >= 100000  
  **When** 系统计算  
  **Then** = KOL
- **Given** 阈值在系统设置中调整（例如改为 5W/20W）  
  **When** 重新触发计算  
  **Then** 新阈值生效

### EP04-S05 系统计算阅读点赞比
| As a | PR |
|---|---|
| I want | 系统自动计算阅读点赞比、收藏赞比 |
| So that | 评估博主互动质量 |
| 阶段 | V1 | 需求 | 2.2, 8 |

**验收标准**

- **Given** 博主有近期阅读量和点赞量数据  
  **When** 系统计算  
  **Then** read_like_ratio = 点赞 / 阅读
- **Given** 阅读量为 0  
  **When** 计算  
  **Then** 返回 null，前端显示"—"

### EP04-S06 假号判断
| As a | PR |
|---|---|
| I want | 系统自动标记疑似假号博主 |
| So that | 避免投放给数据造假的博主 |
| 阶段 | V1 | 需求 | 2.2, 8 |

**验收标准**

- **Given** 博主 read_like_ratio <= 假号阈值（默认 0.1）  
  **When** 系统计算  
  **Then** is_fake_account = false
- **Given** 阈值在系统设置中可配置  
  **When** 调整阈值  
  **Then** 重新计算所有博主标记

### EP04-S07 质量标签
| As a | PR |
|---|---|
| I want | 系统给博主打"高性价比"、"带货型"等质量标签 |
| So that | 决策更直观 |
| 阶段 | V1 | 需求 | 2.2, 8 |

**验收标准**

- **Given** 博主历史推广 CPL ≤ 阈值（高性价比阈值）  
  **When** 系统计算  
  **Then** quality_tags 含"高性价比"
- **Given** 多个标签条件满足  
  **When** 计算  
  **Then** quality_tags 是数组，多标签并存

### EP04-S08 灰豚画像数据展示
| As a | PR |
|---|---|
| I want | 在博主详情中看到灰豚同步的粉丝画像、笔记数据 |
| So that | 选博主时参考画像匹配度 |
| 阶段 | V1 | 需求 | 2.2, 2.7 | Journey | J3 |

**验收标准**

- **Given** 灰豚采集已同步该博主数据  
  **When** 查询博主详情  
  **Then** 返回 audience_profile（性别/年龄/地域占比）、note_stats（爆文率、活跃粉丝数）
- **Given** 灰豚未同步过该博主  
  **When** 查询  
  **Then** 画像字段返回 null，前端显示"暂无灰豚数据"

---

## EP05：推广合作生命周期

业务目标：覆盖推广合作从录入、催发、发布、召回到结算的全生命周期，是系统最高频使用的核心模块。

### EP05-S01 推广合作 Epic 总览
| As a | PR / PR 主管 |
|---|---|
| I want | 在统一界面管理所有推广合作记录 |
| So that | 替代原 Excel 5494 行手动维护 |
| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |

**验收标准**

- **Given** PR 已登录  
  **When** 进入"站外推广 2026"页面  
  **Then** 看到推广列表（默认按 cooperation_date DESC）
- **Given** 列表筛选支持多条件（PR、博主、款号、催发状态、发布状态、日期范围）  
  **When** 筛选  
  **Then** 返回过滤结果
- **Given** 状态机定义见需求 2.3 / 11.3 / 13.2  
  **When** 各角色操作  
  **Then** publish_status / recall_status / settlement_status 按状态机推进

### EP05-S02 PR 创建推广合作
| As a | PR |
|---|---|
| I want | 录入新推广（品名、博主、报价、合作日期、预定发布日期等） |
| So that | 启动一笔合作并自动生成内部编码 |
| 阶段 | MVP | 需求 | 2.3, 13.2 | Journey | J2 |

**验收标准**

- **Given** PR 已登录，提供必填字段  
  **When** 调用 `POST /api/promotions/`  
  **Then** 创建 promotion，internal_code 自动生成（按规则 `<tenant_prefix><yyMMdd><sequence>`），UNIQUE (tenant_id, internal_code)
- **Given** style_id 不存在  
  **When** 创建  
  **Then** 返回 422
- **Given** blogger_id 不存在  
  **When** 创建  
  **Then** 返回 422
- **Given** 必填字段（合作日期、博主、款式）缺失  
  **When** 创建  
  **Then** 返回 422 并列出缺失字段

### EP05-S03 自动按款号填充商品简称
（详见 EP02-S06，本故事在推广创建场景下复用）

| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |
|---|---|---|---|---|

**验收标准**

- **Given** PR 在录入页输入 style_code  
  **When** 失焦或调用 `GET /api/styles/by-code/{code}`  
  **Then** 自动填充商品简称、总成本（按 SKU 聚合）

### EP05-S04 同款博主重复检测
| As a | PR |
|---|---|
| I want | 录入时如发现同一款号 + 同一博主已有合作，自动标记重复 |
| So that | 避免重复投放 |
| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |

**验收标准**

- **Given** promotion 表已有 (style_id=W001, blogger_id=B1) 的活跃记录  
  **When** PR 再次录入相同组合  
  **Then** 返回 warning（不阻塞），前端弹出确认对话框"是否确认重复合作？"

### EP05-S05 双平台标记
| As a | PR |
|---|---|
| I want | 同款号在不同平台（小红书+抖音）合作时自动标记"双平台" |
| So that | 报表中识别此类高曝光款 |
| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |

**验收标准**

- **Given** 同 style_id 的 promotion 中存在不同 platform 的记录  
  **When** 系统计算 dual_platform 标记  
  **Then** 这些 promotion 都被标记为 dual_platform=true

### EP05-S06 实时计算催发状态
| As a | PR |
|---|---|
| I want | 系统实时计算每条推广的催发状态 |
| So that | 看一眼就知道哪些需要催 |
| 阶段 | MVP | 需求 | 2.3, 11.3, 13.2 | Journey | J2 |

**验收标准**

- **Given** publish_status="已取消"  
  **When** 查询  
  **Then** urge_status="已取消"
- **Given** publish_status="已发布"  
  **When** 查询  
  **Then** urge_status="已发布"
- **Given** publish_status="未发布"，scheduled_publish_date 为空  
  **When** 查询  
  **Then** urge_status="未排期"
- **Given** publish_status="未发布"，今天 < scheduled_publish_date - 催发天数(默认10)  
  **When** 查询  
  **Then** urge_status="档期内"
- **Given** scheduled_publish_date - 催发天数 ≤ 今天 < scheduled_publish_date - 重要催发天数(默认3)  
  **When** 查询  
  **Then** urge_status="催发"
- **Given** scheduled_publish_date - 重要催发天数 ≤ 今天 ≤ scheduled_publish_date  
  **When** 查询  
  **Then** urge_status="重要催发"
- **Given** 今天 > scheduled_publish_date  
  **When** 查询  
  **Then** urge_status="超时"

### EP05-S07 PR 填入发布链接
| As a | PR |
|---|---|
| I want | 填入小红书/抖音笔记链接，标记已发布 |
| So that | 触发后续审核和结算 |
| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |

**验收标准**

- **Given** publish_status="未发布"  
  **When** 调用 `PUT /api/promotions/{id}/publish`，body 含 publish_url、publish_date  
  **Then** publish_status="已发布"，触发企微发文通知（EP08-S09）
- **Given** publish_url 不是合法 URL  
  **When** 提交  
  **Then** 返回 422

### EP05-S08 PR 取消合作
| As a | PR |
|---|---|
| I want | 取消尚未发布的合作 |
| So that | 处理博主放鸽子或临时调整的情况 |
| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |

**验收标准**

- **Given** publish_status="未发布"  
  **When** 调用 `PUT /api/promotions/{id}/cancel`，body 含 cancel_reason  
  **Then** publish_status="已取消"，audit_log 记录原因
- **Given** publish_status="已发布"  
  **When** 尝试取消  
  **Then** 返回 422，提示已发布合作请走"召回"流程

### EP05-S09 PR 发起召回
| As a | PR |
|---|---|
| I want | 对已取消或异常合作发起召回 |
| So that | 追回已支付的样品或费用 |
| 阶段 | MVP | 需求 | 2.3 | Journey | J2 |

**验收标准**

- **Given** publish_status="已取消"  
  **When** 调用 `PUT /api/promotions/{id}/recall`  
  **Then** recall_status="召回中"
- **Given** recall_status="召回中"  
  **When** 标记召回成功/失败  
  **Then** recall_status 推进，召回成功不可逆，召回失败可重新发起

### EP05-S10 系统计算有效点赞量
| As a | PR |
|---|---|
| I want | 系统按平台折算点赞量（如抖音÷10） |
| So that | 跨平台 CPL 可比 |
| 阶段 | MVP | 需求 | 2.3, 8 |

**验收标准**

- **Given** platform="douyin"，like_count=1000，抖音折算系数=10  
  **When** 系统计算 effective_like_count  
  **Then** = 100
- **Given** platform="xiaohongshu"，like_count=500  
  **When** 计算  
  **Then** = 500（不折算）
- **Given** 折算系数在系统设置中调整  
  **When** 调整  
  **Then** 新增/编辑的 promotion 用新系数；历史数据不重算（保留原值）

### EP05-S11 爆文标记
| As a | PR |
|---|---|
| I want | 已发布且点赞数 ≥ 爆文阈值的笔记自动标记为爆文 |
| So that | 报表能识别爆款 |
| 阶段 | MVP | 需求 | 2.3, 8 |

**验收标准**

- **Given** like_count >= 爆文阈值（默认 1000）  
  **When** 查询  
  **Then** is_hit=true
- **Given** 阈值调整  
  **When** 重新查询  
  **Then** 标记按新阈值实时计算

### EP05-S12 计算单件点赞成本
| As a | PR |
|---|---|
| I want | 系统计算每条推广的单件点赞成本 |
| So that | 评估投放性价比 |
| 阶段 | MVP | 需求 | 2.3 |

**验收标准**

- **Given** 总成本 1000，effective_like_count 100  
  **When** 计算 cpl  
  **Then** = 10
- **Given** effective_like_count = 0  
  **When** 计算  
  **Then** 返回 null，前端显示"—"

### EP05-S13 PR 主管审核推广
| As a | PR 主管 |
|---|---|
| I want | 审核 PR 已发布的推广，触发结算 |
| So that | 数据无误后才进入财务流程 |
| 阶段 | MVP | 需求 | 2.3, 13.3 | Journey | J2 J4 |

**验收标准**

- **Given** publish_status="已发布"  
  **When** PR 主管调用 `POST /api/promotions/{id}/review`，action="approve"  
  **Then** 自动生成 settlement 记录（settlement_status="待付款"），按推广报价填入金额
- **Given** PR 主管 action="reject"，附 reason  
  **When** 调用  
  **Then** settlement_status="已驳回"，通知 PR 修改

---

## EP06：财务结款 / 拍单 / 刷单 / 余额

业务目标：覆盖佣金结算、拍单付款、刷单隔离、余额核对的完整财务流程。

### EP06-S01 财务结款 Epic 总览
| As a | PR 主管 / 财务 |
|---|---|
| I want | 在统一界面管理结算单全生命周期 |
| So that | 替代原 Excel 1428 行手动维护 |
| 阶段 | MVP | 需求 | 2.4 | Journey | J4 |

**验收标准**

- **Given** PR 主管或财务登录  
  **When** 进入"财务结款"  
  **Then** 看到结算列表（按 settlement_status 分组：待核查/待付款/待财务付款/已付款/已驳回）
- **Given** 结算流程见需求 2.4 / 11.3 / 13.3  
  **When** 各角色按权限操作  
  **Then** 状态推进或回退记录在 audit_log

### EP06-S02 自动生成结算单
| As a | PR 主管 |
|---|---|
| I want | PR 主管审核通过推广后系统自动生成结算单 |
| So that | 不漏算佣金 |
| 阶段 | MVP | 需求 | 2.4, 13.3 | Journey | J4 |

**验收标准**

- **Given** PR 主管审核通过 promotion  
  **When** 系统触发  
  **Then** 创建 settlement，关联 promotion_id，金额按 promotion.quote_amount 填入，settlement_status="待付款"
- **Given** 同一 promotion 已有 settlement  
  **When** 再次触发  
  **Then** 不重复创建（按 promotion_id 幂等）

### EP06-S03 PR 主管核查结算单
（实际是 EP05-S13 的下游动作，本故事独立列出便于追踪）

| 阶段 | MVP | 需求 | 2.4 | Journey | J4 |
|---|---|---|---|---|

**验收标准**

- **Given** settlement_status="待核查"  
  **When** PR 主管 action="approve"  
  **Then** settlement_status="待付款"

### EP06-S04 PR 主管驳回结算
| As a | PR 主管 |
|---|---|
| I want | 发现金额错误时驳回 |
| So that | 不让财务付错钱 |
| 阶段 | MVP | 需求 | 2.4 | Journey | J4 |

**验收标准**

- **Given** settlement_status="待核查" 或"待付款"  
  **When** 调用 `PUT /api/settlements/{id}/review`，action="reject"，附 reason  
  **Then** settlement_status="已驳回"，通知 PR

### EP06-S05 PR 主管增加结算项
| As a | PR 主管 |
|---|---|
| I want | 在结算时增加运费、赞奖等额外费用项 |
| So that | 一次结清，不漏付 |
| 阶段 | MVP | 需求 | 2.4 | Journey | J4 |

**验收标准**

- **Given** settlement_status="待付款"  
  **When** 调用 `POST /api/settlements/{id}/extra-items`，body 含 item_type=运费/赞奖、amount  
  **Then** 写入 settlement_extra_item，total 重算
- **Given** 用户角色不是 PR 主管  
  **When** 调用  
  **Then** 返回 403

### EP06-S06 PR 主管填写付款金额
| As a | PR 主管 |
|---|---|
| I want | 确认付款金额并提交财务 |
| So that | 财务知道要付多少 |
| 阶段 | MVP | 需求 | 2.4, 13.3 | Journey | J4 |

**验收标准**

- **Given** settlement_status="待付款"  
  **When** 调用 `PUT /api/settlements/{id}/payment-amount`，body 含 payment_amount  
  **Then** settlement.payment_amount 写入，settlement_status="待财务付款"，通知财务

### EP06-S07 财务上传付款截图
| As a | 财务 |
|---|---|
| I want | 上传付款截图标记已付款 |
| So that | 留痕，事后可对账 |
| 阶段 | MVP | 需求 | 2.4, 13.3 | Journey | J4 |

**验收标准**

- **Given** settlement_status="待财务付款"  
  **When** 调用 `PUT /api/settlements/{id}/payment-proof`，上传截图 + payment_date  
  **Then** 截图存 R2 private 桶，settlement_status="已付款"
- **Given** 付款金额、日期、截图任一缺失  
  **When** 提交  
  **Then** 返回 422，data_quality_issue 写 error 级别记录
- **Given** 已付款的结算  
  **When** 尝试再次上传  
  **Then** 返回 422，"已付款不可重复"

### EP06-S08 当日结算汇总
| As a | PR 主管 / 财务 |
|---|---|
| I want | 看当日结算汇总（待核查、待付款、已付款、合计金额） |
| So that | 月底对账简单 |
| 阶段 | MVP | 需求 | 2.4 | Journey | J4 |

**验收标准**

- **Given** PR 主管或财务登录  
  **When** 调用 `GET /api/settlements/daily-summary?date=2026-05-24`  
  **Then** 返回各状态的数量和金额合计
- **Given** 用户无该 API 权限  
  **When** 调用  
  **Then** 返回 403

### EP06-S09 拍单自动生成
| As a | 财务 |
|---|---|
| I want | 推广记录"店铺拍单=是"时系统自动生成拍单记录 |
| So that | 不用手工录拍单 |
| 阶段 | V2 | 需求 | 2.4 |

**验收标准**

- **Given** promotion 含 in_store_order=true  
  **When** PR 主管审核通过  
  **Then** 创建 order_adjustment，order_type="拍单"，按 promotion 关联自动填充博主和商品
- **Given** 用户内部编码  
  **When** 拍单详情页查询  
  **Then** 自动展示博主、商品信息

### EP06-S10 刷单录入与 ROI 隔离
| As a | 财务 |
|---|---|
| I want | 录入刷单记录并自动从 ROI 中剔除 |
| So that | 投产报表反映真实 ROI |
| 阶段 | V2 | 需求 | 2.4, 11.6 |

**验收标准**

- **Given** 财务录入刷单 (order_type="刷单")  
  **When** 创建  
  **Then** exclude_from_roi=true（默认）
- **Given** 金额格式为"原价-返现"如"100-30"  
  **When** 系统解析  
  **Then** amount = 70
- **Given** 投产报表计算 ROI  
  **When** 聚合销售额  
  **Then** 排除所有 exclude_from_roi=true 的订单

### EP06-S11 余额核对
| As a | 财务 |
|---|---|
| I want | 维护余额流水（充值、推广支出、刷拍单支出） |
| So that | 账面余额随时对得上 |
| 阶段 | V2 | 需求 | 2.4 |

**验收标准**

- **Given** balance_record 列表  
  **When** 财务录入新一笔（充值或支出）  
  **Then** 当前余额 = 上一笔余额 + 收入 - 支出，自动计算
- **Given** 计算结果与人工填写不一致  
  **When** 系统校验  
  **Then** 标红报错，不允许保存
- **Given** 充值行只填收入，推广行只填支出  
  **When** 录入  
  **Then** 类型与字段匹配，错配返回 422

---

## EP07：数据采集与统一导入

业务目标：建立"统一导入入口"+"自动数据采集"双链路，所有外部数据通过同一管道入库。

> **实现备注（不暴露给用户）**：自动数据采集 Worker 的内部实现使用浏览器自动化（如 Playwright/Selenium）完成平台登录与导出，外层接口仅称"采集 Worker"或"自动数据采集服务"。具体技术选型属于 Construction 阶段决策。

### EP07-S01 数据采集 Epic 总览
| As a | 管理员 / PR / 运营 |
|---|---|
| I want | 看到所有数据导入的统一入口（手动 + 自动） |
| So that | 数据来源清晰可追溯 |
| 阶段 | MVP | 需求 | 2.7, 12 | Journey | J3 |

**验收标准**

- **Given** 管理员登录  
  **When** 进入"数据导入"页面  
  **Then** 看到 import_batch 列表（含来源、状态、行数、时间）和"上传"按钮
- **Given** 状态机和链路定义见需求 2.7  
  **When** 任意来源（手动上传 / 采集 Worker）触发导入  
  **Then** 都走 import_batch → import_job → field_mapping → 校验 → 入库

### EP07-S02 管理员添加平台凭据
| As a | 管理员 |
|---|---|
| I want | 添加千牛/万相台/灰豚的平台凭据（账号密码） |
| So that | 采集 Worker 能登录采集 |
| 阶段 | V1 | 需求 | 12.1, 12.2 |

**验收标准**

- **Given** 管理员已登录，已确认隐私提示  
  **When** 调用 `POST /api/settings/credentials`  
  **Then** 凭据 AES-256 加密入库，返回 credential_id（不含明文）
- **Given** 用户未确认隐私提示  
  **When** 提交  
  **Then** 返回 422 "请先确认隐私协议"
- **Given** 默认 credential.status="paused"（用户需主动启用）

### EP07-S03 凭据加密存储且不可回显
| As a | 管理员 |
|---|---|
| I want | 凭据明文密码任何 API、日志都不返回 |
| So that | 即便接口被滥用也不泄露密码 |
| 阶段 | V1 | 需求 | 12.3, 13.7 |

**验收标准**

- **Given** 凭据已存储  
  **When** 调用 `GET /api/settings/credentials/{id}`  
  **Then** 返回字段不含 password，含 username、platform、status、updated_at
- **Given** 任何接口、错误响应、日志  
  **When** 检查响应内容  
  **Then** 不出现明文密码
- **Given** 采集 Worker 解密凭据  
  **When** 用完  
  **Then** 内存立即释放（不写入持久化日志）

### EP07-S04 凭据解密审计
| As a | 管理员 |
|---|---|
| I want | 每次解密凭据都写审计日志 |
| So that | 异常解密能追责 |
| 阶段 | V1 | 需求 | 12.4, 13.7 |

**验收标准**

- **Given** 采集 Worker 启动采集任务  
  **When** 解密 credential  
  **Then** audit_log 写入：tenant_id, user_id（如人工触发可空）, credential_id, platform, operation="decrypt", purpose, timestamp
- **Given** 任何用户  
  **When** 尝试 DELETE/UPDATE audit_log  
  **Then** 数据库层拒绝（append-only）

### EP07-S05 管理员暂停/删除凭据
| As a | 管理员 |
|---|---|
| I want | 随时暂停或删除凭据 |
| So that | 平台账号被封时立即停采集，避免风险 |
| 阶段 | V1 | 需求 | 12.5 |

**验收标准**

- **Given** credential.status="active"  
  **When** 调用 `PUT /api/settings/credentials/{id}/pause`  
  **Then** status="paused"，相关采集任务跳过该凭据，audit_log 记录
- **Given** credential 存在  
  **When** 调用 `DELETE /api/settings/credentials/{id}`  
  **Then** 凭据从存储清除，相关定时任务自动停止，audit_log 记录

### EP07-S06 采集失败告警
| As a | 管理员 |
|---|---|
| I want | 自动采集失败时（登录失败/验证码/IP 风控）立即收到告警 |
| So that | 及时处理避免账号被封 |
| 阶段 | V1 | 需求 | 12.6 |

**验收标准**

- **Given** 采集 Worker 一次采集失败  
  **When** 任务结束  
  **Then** 写入 import_batch.status="failed" + error_detail
- **Given** 同一凭据连续失败 N 次（默认 3）  
  **When** 触发告警  
  **Then** credential.status 自动置为"paused"，企微通知管理员

### EP07-S07 手动上传 Excel/CSV
| As a | PR / 运营 |
|---|---|
| I want | 直接上传 Excel/CSV 文件做数据导入 |
| So that | 在 自动采集故障或临时数据补录时使用 |
| 阶段 | MVP | 需求 | 2.7, 13.1 |

**验收标准**

- **Given** 用户已登录，提供合法 CSV/Excel  
  **When** 调用 `POST /api/import/upload`，multipart 包含 file + source  
  **Then** 创建 import_batch（status="processing"），异步触发 Celery 任务解析入库
- **Given** 任务完成  
  **When** 查询 batch  
  **Then** 返回 imported / failed 数；失败行写入 import_job

### EP07-S08 文件 hash 去重
| As a | PR / 运营 |
|---|---|
| I want | 同一文件不重复导入 |
| So that | 避免人为重复上传导致数据翻倍 |
| 阶段 | MVP | 需求 | 2.7, 13.1 |

**验收标准**

- **Given** 已存在同 file_hash（SHA256）的 import_batch  
  **When** 再次上传相同文件  
  **Then** 返回 409，提示"该文件已导入（batch_id=...）"

### EP07-S09 字段映射版本管理
| As a | 管理员 |
|---|---|
| I want | 平台字段格式变化时新增映射版本 |
| So that | 历史数据按旧映射保留，新数据按新映射处理 |
| 阶段 | MVP | 需求 | 2.7 |

**验收标准**

- **Given** 字段映射已有 v1  
  **When** 管理员上传 v2 mapping_config  
  **Then** field_mapping 表新增 v2 记录，旧 v1 不删除
- **Given** 当前生效映射 = v2  
  **When** 新导入  
  **Then** 按 v2 解析；查询历史 batch 时记录使用的版本

### EP07-S10 失败行下载与重试
| As a | PR / 运营 |
|---|---|
| I want | 下载失败行明细，修正后重试 |
| So that | 不用重新上传整个文件 |
| 阶段 | MVP | 需求 | 2.7, 13.1 |

**验收标准**

- **Given** import_batch.failed > 0  
  **When** 调用 `GET /api/import/batches/{id}/errors/download`  
  **Then** 下载 CSV，含原始数据 + error_detail
- **Given** 用户调用 `POST /api/import/batches/{id}/retry`  
  **When** 触发  
  **Then** 仅重试 import_job.status="failed" 的行；指数退避（1s/5s/30s），最多 3 次

### EP07-S11 自动同步千牛
| As a | 管理员 |
|---|---|
| I want | 采集 Worker 定时登录千牛抓取商品日报 |
| So that | 用户无需手动操作 |
| 阶段 | V1 | 需求 | 2.7 | Journey | J3 |

**验收标准**

- **Given** 千牛凭据已配置且 status="active"  
  **When** Celery Beat 触发（默认每天 02:00）  
  **Then** 采集 Worker 解密凭据 → 登录千牛 → 导出昨日数据 → 调用 `POST /api/import/upload` source="qianniu"
- **Given** 数据成功入库  
  **When** 查询 qianniu_daily  
  **Then** 当日数据可见，UNIQUE (tenant_id, platform_product_id, date) 幂等

### EP07-S12 自动同步万相台
（同 S11，目标平台不同，source="wanxiangtai"）

| 阶段 | V1 | 需求 | 2.7 | Journey | J3 |
|---|---|---|---|---|

**验收标准**

- **Given** 万相台凭据已配置  
  **When** 定时触发  
  **Then** 数据入 ad_daily 表

### EP07-S13 自动同步灰豚
（同 S11，source="huitun"，目标 blogger 表）

| 阶段 | V1 | 需求 | 2.7 | Journey | J3 |
|---|---|---|---|---|

**验收标准**

- **Given** 灰豚凭据已配置  
  **When** 定时触发  
  **Then** 博主画像数据更新到 blogger 表

### EP07-S14 数据质量看板
| As a | 管理员 |
|---|---|
| I want | 看所有 data_quality_issue 的汇总（按来源、严重度、状态） |
| So that | 主动发现并修复异常 |
| 阶段 | V1 | 需求 | 4.3 |

**验收标准**

- **Given** data_quality_issue 表有数据  
  **When** 调用 `GET /api/data-quality/summary`  
  **Then** 返回按 source × severity 分组的计数
- **Given** 管理员标记某条 issue 为"已修复"或"已忽略"  
  **When** 调用更新接口  
  **Then** issue.status 变更，audit_log 记录

---

## EP08：企业微信集成

业务目标：自动化博主催发流程，配合频控降级和审计追踪。

### EP08-S01 企微集成 Epic 总览
| As a | PR / 管理员 |
|---|---|
| I want | 在系统中统一管理企微催发、群发、回调 |
| So that | 替代手工发微信消息 |
| 阶段 | MVP | 需求 | 2.8 | Journey | J2 |

**验收标准**

- **Given** 企微应用已配置（EP08-S02）  
  **When** 触发催发或发文通知  
  **Then** 调用企微 API，wecom_message 记录状态
- **Given** 各种状态见需求 2.8  
  **When** 状态变更  
  **Then** 按 pending/created/sent/rejected/rate_limited/failed 推进

### EP08-S02 配置企微自建应用
| As a | 管理员 |
|---|---|
| I want | 录入企微 corp_id、secret、agent_id 等参数 |
| So that | 系统能调用企微 API |
| 阶段 | MVP | 需求 | 2.8 |

**验收标准**

- **Given** 管理员已登录  
  **When** 调用 `PUT /api/settings/wecom`  
  **Then** secret 字段加密存储（同凭据机制），界面不回显明文
- **Given** 配置完成  
  **When** 调用 `POST /api/settings/wecom/test`  
  **Then** 返回测试结果（access_token 是否能获取）

### EP08-S03 博主企微外部联系人绑定
| As a | PR |
|---|---|
| I want | 把博主的微信号匹配到企微外部联系人（external_userid） |
| So that | 企微群发能精准发到该博主 |
| 阶段 | MVP | 需求 | 2.8 |

**验收标准**

- **Given** 博主有 wechat_id  
  **When** 调用 `POST /api/bloggers/{id}/wecom-bind`  
  **Then** 系统调用企微 API 按 wechat_id 查 external_userid，写入 blogger.wecom_external_userid
- **Given** 微信号未在企微外部联系人中  
  **When** 绑定  
  **Then** 返回 404，提示先在企微端添加该联系人

### EP08-S04 编辑催发消息模板
| As a | 管理员 |
|---|---|
| I want | 自由编辑催发模板（支持变量 {博主昵称} {商品简称} 等） |
| So that | 文案可灵活调整 |
| 阶段 | MVP | 需求 | 2.8 |

**验收标准**

- **Given** 管理员已登录  
  **When** 调用 `PUT /api/settings/templates/urge`，body 含模板文本  
  **Then** 模板保存，下次催发使用新模板
- **Given** 模板含未支持的变量  
  **When** 提交  
  **Then** 返回 422，列出非法变量

### EP08-S05 自动催发扫描定时任务
| As a | 管理员 |
|---|---|
| I want | 系统每天定时扫描需要催发的推广 |
| So that | 不漏催 |
| 阶段 | MVP | 需求 | 2.8, 8 | Journey | J2 |

**验收标准**

- **Given** Celery Beat 定时（默认每天 09:00）  
  **When** 触发扫描任务  
  **Then** 筛选 urge_status ∈ {催发, 重要催发, 超时} 且未取消未发布的推广，按博主聚合
- **Given** 扫描完成  
  **When** 写入 wecom_message  
  **Then** status="pending"，等待发送

### EP08-S06 触发催发企微群发
| As a | PR |
|---|---|
| I want | 系统自动通过企微群发助手 API 发送催发消息给博主 |
| So that | 不用手动一一私聊 |
| 阶段 | MVP | 需求 | 2.8, 13.4 | Journey | J2 |

**验收标准**

- **Given** wecom_message.status="pending"，未触发频控  
  **When** Celery Worker 调用企微 `add_msg_template` API  
  **Then** status="created"（待 PR 在企微端确认）
- **Given** PR 在企微端确认发送  
  **When** 企微回调通知系统  
  **Then** wecom_message.status="sent"

### EP08-S07 频控降级到站内通知
| As a | PR |
|---|---|
| I want | 当企微频控（每博主每天 1 条 / 每 PR 每天 1 次）触发时，系统降级为站内通知 |
| So that | 不漏催发，避免账号违规 |
| 阶段 | MVP | 需求 | 2.8, 13.4 | Journey | J2 |

**验收标准**

- **Given** 某博主当天已收到 1 条群发  
  **When** 系统再次尝试  
  **Then** wecom_message.status="rate_limited"，同时写一条 notification 给该 PR 内容="请手动催发 {博主昵称}"
- **Given** 某 PR 当天已发起 1 次群发  
  **When** 系统以该 PR 名义再次发起  
  **Then** 整批降级为站内通知

### EP08-S08 企微回调更新消息状态
| As a | 管理员 |
|---|---|
| I want | 接收企微的发送结果回调 |
| So that | 知道哪些消息真的发出去了 |
| 阶段 | MVP | 需求 | 2.8 |

**验收标准**

- **Given** 系统已配置回调 URL  
  **When** 企微推送回调（含 msgid 和 result）  
  **Then** wecom_message.status 按 result 更新（sent/rejected/failed）
- **Given** 回调签名校验失败  
  **When** 接收回调  
  **Then** 返回 403，audit_log 记录可疑请求

### EP08-S09 发文通知控评
| As a | PR |
|---|---|
| I want | 笔记发布后系统自动通知控评群 |
| So that | 控评能及时跟进 |
| 阶段 | V1 | 需求 | 2.8 |

**验收标准**

- **Given** PR 填入 publish_url  
  **When** publish_status 变为"已发布"  
  **Then** 系统通过企微群聊机器人 API 推送消息到指定群
- **Given** 群机器人 webhook 未配置  
  **When** 触发  
  **Then** 不阻塞主流程，记录 warning

### EP08-S10 异常预警推送管理群
| As a | 管理员 |
|---|---|
| I want | 退货率/转化率/投产比异常时推送管理群 |
| So that | 团队及时响应 |
| 阶段 | V1 | 需求 | 2.8 |

**验收标准**

- **Given** 退货率 > 阈值（默认 40%）  
  **When** 监控任务触发  
  **Then** 通过企微自建应用推送管理群，含异常详情和建议
- **Given** 阈值在系统设置中可配置  
  **When** 调整  
  **Then** 新阈值立即生效

---

## EP09：报表与看板

业务目标：把分散在多个 Excel 的报表统一到系统中，提供时间筛选、可视化和导出能力。

### EP09-S01 发文进度三层看板
| As a | PR / PR 主管 |
|---|---|
| I want | 一站式查看推广发布进度（全局汇总 + 商品卡片 + 详情面板） |
| So that | 替代原 Excel 389 行 |
| 阶段 | MVP | 需求 | 2.5, 13.6 | Journey | J3 |

**验收标准**

- **Given** 已有推广数据  
  **When** 进入"发文进度表"页面  
  **Then** 第一层显示全局汇总（约篇金额/约篇量/合作金额/发布量/发布率/超时率/点赞量/点赞成本/取消量），颜色按阈值区分（绿/黄/红）
- **Given** 第二层商品卡片  
  **When** 渲染  
  **Then** 每商品一卡片（PC 每行 4 个），含图片、品名、颜色、款号、成本、约篇量/金额、发布量/金额、取消量、点赞量、超时量、点赞成本，发布率/超时率进度条可视化
- **Given** 用户点击卡片  
  **When** 打开右侧抽屉  
  **Then** Tab1 显示 PR 维度明细，Tab2 显示半月周期趋势 + 折线图
- **Given** 时间筛选支持近 7 天/30 天/本月/上月/自定义  
  **When** 切换  
  **Then** 三层数据按 cooperation_date 字段重新聚合
- **Given** 分母为 0 的指标  
  **When** 渲染  
  **Then** 显示"—"

### EP09-S02 工作进度表
| As a | PR 主管 |
|---|---|
| I want | 按月份和 PR 维度查看工作量 KPI |
| So that | 月度考核有据可依 |
| 阶段 | V1 | 需求 | 2.5 |

**验收标准**

- **Given** 当前月份 + 各 PR  
  **When** 调用 `GET /api/reports/work-progress?month=2026-05`  
  **Then** 返回每个 PR 的：约篇件数、档期内/催发/重要催发/超时/已发布数、信息完整度、已取消、应召回、召回成功、召回完成率、超时率、月度完成率、爆文数（≥爆文统计阈值默认 500）、爆文率、点赞数、成本、CPL
- **Given** 时间筛选切换月份  
  **When** 重查  
  **Then** 数据按所选月份重算，按 cooperation_date 聚合

### EP09-S03 爆款约篇数量
| As a | PR 主管 |
|---|---|
| I want | 设置每款的最低约篇目标并跟踪达标情况 |
| So that | 推广有量化目标 |
| 阶段 | V1 | 需求 | 2.5 |

**验收标准**

- **Given** PR 主管设置 (PR=张三, style=W001, 最低约篇=10, 月份=2026-05)  
  **When** 系统聚合该月 promotion 数据  
  **Then** 返回实际约篇数、状态(达标/未达标)、缺口/超额
- **Given** 在发文进度表卡片上看到约篇目标提示

### EP09-S04 店铺数据看板
| As a | 运营 |
|---|---|
| I want | 千牛数据按日聚合看板 |
| So that | 替代原 Excel 127 行 |
| 阶段 | V1 | 需求 | 2.5 |

**验收标准**

- **Given** 千牛日报已入库  
  **When** 进入店铺数据看板  
  **Then** 24 列字段从 qianniu_daily 表 SUMIF 聚合显示（按 date）
- **Given** 手动输入字段（全站推消耗、直通车消耗、引力魔方消耗）  
  **When** 用户编辑  
  **Then** 直接更新该日记录
- **Given** 时间筛选近 7 天/30 天/自定义  
  **When** 切换  
  **Then** 聚合范围按所选

### EP09-S05 投产报表
| As a | 运营 |
|---|---|
| I want | 按款式维度看全链路投产数据 |
| So that | 决策投放调整 |
| 阶段 | V1 | 需求 | 2.5, 13.6 |

**验收标准**

- **Given** 跨表数据（千牛、站外推广、单品站内推广、发文进度）  
  **When** 调用 `GET /api/reports/production?date_from=...&date_to=...`  
  **Then** 按款式聚合返回 70 列字段，含核心计算指标：
  - 退货退款率 = 成功退款金额 / 支付金额
  - 待确认收货金额 = 支付金额 - 成功退款金额
  - 加购成本 = (站外成本 + 站内投放) / 总加购数
  - 净投产比 = 待确认收货金额 / 推广总花费
  - 推广单件成交成本 = 加购成本 / 加购转化率 / (1-退货率)
- **Given** 任意分母为 0  
  **When** 计算  
  **Then** 返回 null，前端显示"—"
- **Given** 周环比数据框  
  **When** 切换时间  
  **Then** 显示当期 + 上期 + 环比变化
- **Given** 包含刷单订单  
  **When** 计算 ROI  
  **Then** 必须排除 exclude_from_roi=true 的订单

### EP09-S06 BI 看板
| As a | 运营 |
|---|---|
| I want | 可视化仪表盘（图表组合） |
| So that | 一眼看懂经营趋势 |
| 阶段 | V2 | 需求 | 2.5 |

**验收标准**

- **Given** 数据已聚合  
  **When** 进入 BI 看板  
  **Then** 显示卡片 + 图表（折线、柱状、饼图）组合
- **Given** 默认布局可配置  
  **When** 用户调整  
  **Then** 保存到 user_preference

### EP09-S07 时间筛选组件
| As a | 全报表角色 |
|---|---|
| I want | 所有报表/看板用统一的时间筛选组件 |
| So that | 操作一致 |
| 阶段 | MVP | 需求 | 2.5 |

**验收标准**

- **Given** 任意报表页面  
  **When** 渲染时间筛选  
  **Then** 提供"近7天/近30天/本月/上月/自定义日期范围"5 个选项
- **Given** 用户切换  
  **When** 触发  
  **Then** 当前页面 API 重查，URL 含时间参数（便于分享）

### EP09-S08 报表导出 Excel
| As a | PR 主管 / 运营 |
|---|---|
| I want | 把任意报表导出为 Excel |
| So that | 离线分析或上报 |
| 阶段 | V2 | 需求 | 2.5 |

**验收标准**

- **Given** 任意报表  
  **When** 调用 `GET /api/reports/{type}/export?format=xlsx`  
  **Then** 返回 Excel 流，含衍生字段，按当前筛选条件
- **Given** 用户无导出权限  
  **When** 调用  
  **Then** 返回 403

---

## EP10：NFR Checklist（非功能需求）

> 说明：本 Epic 不展开成 INVEST 故事，仅作为 NFR 条目占位（按 Q7=C 决策）。每条用编号 `EP10-NFRxx`，明确不进入"可实施故事"清单。详细设计在 Construction 阶段的 NFR Requirements / NFR Design 中处理。

### EP10-NFR01 性能 NFR
| 阶段 | 全阶段 | 需求 | 3.1 |
|---|---|---|---|

- API 响应时间 P95 ≤ 500ms
- 页面加载 ≤ 3秒
- 并发用户数 ≥ 50

### EP10-NFR02 安全 NFR
| 阶段 | 全阶段 | 需求 | 3.3 |
|---|---|---|---|

- JWT + bcrypt 认证
- 完整 RBAC + 字段级权限 + PostgreSQL RLS
- HTTPS 全程
- API 限流 (slowapi)
- CORS 仅允许 app.clothinkai.com
- AES-256 加密凭据，密钥分离

### EP10-NFR03 多租户隔离 NFR
| 阶段 | MVP | 需求 | 3.4, 11.1 |
|---|---|---|---|

- 共享数据库 + tenant_id
- 所有业务唯一键带 tenant_id
- ORM 自动注入 tenant_id 过滤
- 核心表启用 RLS
- R2 文件路径按租户隔离

### EP10-NFR04 备份与恢复 NFR
| 阶段 | MVP | 需求 | 3.2 |
|---|---|---|---|

- 每日 pg_dump → R2
- 30 天每日 + 每月 1 份保留 1 年
- RPO ≤ 24 小时
- RTO ≤ 4 小时
- 每季度恢复演练

### EP10-NFR05 测试覆盖 NFR
| 阶段 | 全阶段 | 需求 | 3.6, 10.5 |
|---|---|---|---|

- 每阶段交付包含单元测试 + 集成测试 + API 测试
- 核心业务逻辑（状态机、催发计算、报表指标）必须有单元测试
- 关键 API（导入、推广、结算、企微、凭据）必须有集成测试

### EP10-NFR06 监控与告警 NFR
| 阶段 | V1 | 需求 | 3.5 |
|---|---|---|---|

- 结构化 JSON 日志
- 监控 API 响应时间、错误率、数据库连接数
- Celery 任务失败 → 企微通知管理员

### EP10-NFR07 Zeabur 服务拆分 NFR
| 阶段 | MVP | 需求 | 6.2 |
|---|---|---|---|

- frontend / backend / celery-worker / celery-beat / postgres / redis 6 个独立服务
- 采集 Worker 可独立部署，与 backend 解耦
- GitHub → Zeabur 自动部署
- 灰度/回滚靠多版本部署 + 流量切换

---

## EP11：AI 决策建议（P3 占位）

> 说明：P3 实验功能，独立交付，不阻塞 MVP/V1/V2 上线。每个故事都是粗粒度占位，具体实现待 P3 阶段细化。

### EP11-S01 AI 推广策略建议
| As a | PR 主管 |
|---|---|
| I want | 系统基于历史推广数据用 DeepSeek V4 给出策略建议 |
| So that | 决策有数据驱动 |
| 阶段 | P3 | 需求 | 2.9 |

**验收标准（粗粒度）**

- **Given** 已有充足历史数据（至少 6 个月推广数据）  
  **When** 调用 `POST /api/ai/strategy-advice`  
  **Then** 返回基于 DeepSeek V4 的建议文本，含数据依据和置信度
- **Given** AI 服务不可用  
  **When** 调用  
  **Then** 优雅降级，返回 503 + 提示信息，不阻塞页面

### EP11-S02 AI 异常原因分析
| As a | 运营 |
|---|---|
| I want | 退货率/转化率异常时让 AI 分析可能原因 |
| So that | 减少手工排查 |
| 阶段 | P3 | 需求 | 2.9 |

**验收标准（粗粒度）**

- **Given** 异常预警触发  
  **When** 用户点击"AI 分析"  
  **Then** 调用 DeepSeek V4，返回多维度归因分析

### EP11-S03 AI 博主选择建议
| As a | PR |
|---|---|
| I want | 录入新推广款时 AI 推荐适合的博主 |
| So that | 提高匹配效率 |
| 阶段 | P3 | 需求 | 2.9 |

**验收标准（粗粒度）**

- **Given** PR 选择款式后  
  **When** 调用 `POST /api/ai/blogger-suggest`  
  **Then** 返回 Top N 博主建议，按匹配度排序，附理由

---

## 一致性校验汇总

| 校验项 | 结果 |
|---|---|
| 可实施故事 ID 唯一 | ✅ 89 个 ID（不含 5 个 Overview / 7 个 NFR / 3 个 P3 占位）无重复 |
| Overview 故事 ID 唯一 | ✅ 5 个（EP03/05/06/07/08-S01） |
| NFR Checklist 编号唯一 | ✅ 7 个（EP10-NFR01 ~ EP10-NFR07） |
| P3 占位故事 ID 唯一 | ✅ 3 个（EP11-S01 ~ EP11-S03） |
| 每可实施故事都有 GWT 验收标准 | ✅ |
| Overview 故事用总览描述代替 GWT | ✅（不参与 Construction 实现） |
| NFR Checklist 用条目代替 GWT | ✅（按 Q7=C 决策） |
| P3 故事用粗粒度 GWT | ✅ |
| 每条目都有阶段标签 | ✅ |
| 每条目都有需求关联 | ✅ |
| 可实施故事 INVEST 校验 | ✅ Independent / Negotiable / Valuable / Estimable / Small / Testable |
| 需求第 10 节阶段定义覆盖 | ✅ MVP/V1/V2/P3 均有故事 |
| 需求第 13 节验收标准对应 | ✅ 13.1→EP07-S07~S10、13.2→EP05-S02/S06、13.3→EP05-S13/EP06-S02/S07、13.4→EP08-S07、13.5→EP01-S06、13.6→EP09-S01/S05、13.7→EP07-S03/S04 |
| 措辞合规 | ✅ 用户故事中无具体 RPA 工具名称暴露，仅在 EP07 实现备注中作为内部技术说明 |

