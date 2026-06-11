# U01 功能设计计划（Functional Design Plan）

> 单元：U01 — 认证 + 多租户基础 + 备份框架  
> 阶段：MVP  
> 覆盖故事：EP01-S01~S04, S07, S08；EP10-NFR03（多租户）；EP10-NFR04（备份任务体）

---

## 概述

本单元功能设计聚焦：
1. **业务实体与关系**：tenant / user / role / permission / user_permission_override / audit_log / refresh_token
2. **业务规则**：登录限流、密码策略、token 生命周期、权限合并、租户隔离一致性
3. **核心算法**：有效权限计算（角色 ∪ 自定义授予 - 自定义撤销）、登录失败计数、备份保留策略

**注意**：技术细节（具体加密算法实现、ORM 钩子代码）属于 NFR Design 和 Code Generation；本文档只给业务逻辑级定义。

---

## 第一部分：决策问题

### Question 1 — 密码策略
密码强度规则？

A) **基础**：≥8 字符，含字母 + 数字
B) **中等**：≥10 字符，含大写 + 小写 + 数字
C) **强**：≥12 字符，含大写 + 小写 + 数字 + 特殊字符
D) Other

[Answer]: B

### Question 2 — 登录失败处理
连续登录失败的处理策略？

A) **5 次失败 → 限流 15 分钟**（IP 维度）
B) **5 次失败 → 锁账户**（用户维度，需管理员解锁）
C) **5 次失败 → 限流 + 第 10 次锁账户**（双层）
D) Other

[Answer]: C

### Question 3 — JWT Token 生命周期
access_token / refresh_token 的有效期？

A) **access 30 分钟 / refresh 7 天**（标准）
B) **access 1 小时 / refresh 14 天**（宽松）
C) **access 15 分钟 / refresh 24 小时**（严格）
D) Other

[Answer]: A

### Question 4 — Token 失效场景
哪些场景需要立即失效现有 token？

A) **仅密码修改**
B) **密码修改 + 用户禁用 + 角色变更**
C) **密码修改 + 用户禁用 + 角色变更 + 权限矩阵变更**
D) Other

[Answer]: C

### Question 5 — 首次登录强制改密
管理员创建用户后，临时密码处理？

A) **首次登录强制改密**（其他 API 在改密前返回 423 Locked）
B) **临时密码 24 小时内必须改**，否则禁用
C) **不强制**，由管理员告知用户
D) Other

[Answer]: A

### Question 6 — 角色与自定义权限冲突
当角色赋予权限 A，但用户级"自定义撤销"也有 A，最终生效如何？

A) **撤销优先**（最严策略：撤销 > 角色 > 授予）
B) **授予优先**（最宽策略：授予 > 角色 > 撤销）
C) **撤销 > 授予 > 角色**（推荐：明确撤销最严，明确授予次之）
D) Other

[Answer]: C

### Question 7 — 多租户隔离的边界异常处理
ORM 查询时如果 Session 没有 tenant_id 上下文（如内部任务、系统初始化），如何处理？

A) **抛 TenantContextMissing 异常**，禁止任何查询
B) **允许显式 system_context() 标记**，只有标记了的代码可以跨租户查询
C) **超级管理员（platform admin）的请求允许跨租户**
D) **B + C 组合**：系统任务用 system_context，平台管理员用专门 token
E) Other

[Answer]: D

### Question 8 — 审计日志的保留与归档
audit_log 数据如何长期管理？

A) **永久保留**（DB 永不删）
B) **DB 保留 1 年，超过部分归档到 R2 backups/audit-archive/**
C) **DB 保留 3 个月，归档到 R2，按月文件**
D) Other

[Answer]: B

### Question 9 — 备份范围
每天 03:00 备份内容？

A) **仅 PostgreSQL（pg_dump 全库）**
B) **PostgreSQL + R2 凭据桶**（确保凭据可恢复）
C) **PostgreSQL + R2 凭据桶 + 关键配置（field_mapping、message_template、permission 矩阵）单独导出**
D) Other

[Answer]: C

### Question 10 — 备份恢复演练
如何实施"每季度恢复演练"（NFR04）？

A) **手动**：在 staging 环境从 R2 拉一个备份恢复，跑核心 smoke test
B) **半自动**：提供 `backend/scripts/restore_backup.py` 脚本 + 验收清单
C) **本阶段不做**，留到 V1/V2 完善
D) Other

[Answer]: B

### Question 11 — 用户 / 角色 / 权限的初始化
首次部署时，谁创建第一个管理员？

A) **启动脚本随机生成 admin 密码并打印到 stdout**（开发文档第 11 节"首次启动随机生成密码，强制修改"）
B) **环境变量** `INITIAL_ADMIN_PASSWORD` 注入
C) **Alembic seed 数据 + 启动检查**（如未存在管理员则创建）
D) Other

[Answer]: C

### Question 12 — 权限范围（scope）的命名规范
权限 scope 的命名规则？

A) **`<module>:<action>`**：如 `style:read`、`promotion:write`、`settlement:approve`
B) **`<module>.<sub>:<action>`**：如 `product.style:read`、`finance.settlement:approve`
C) **`<api_path_pattern>`**：如 `GET:/api/styles/*`
D) Other

[Answer]: B

### Question 13 — 字段级权限的标识方式
字段级权限的 `field_path` 格式（U01 仅定义规范，U09 实施）？

A) **点路径**：`sku.cost_price`、`promotion.quote_amount`、`settlement.payment_amount`
B) **scope:field**：`sku:cost_price`、`promotion:quote_amount`
C) **JSONPath**：`$.sku.cost_price`
D) Other

[Answer]: A

### Question 14 — 默认角色权限基线
9 个预设角色的默认权限矩阵从哪里来？

A) **代码常量**（`app/modules/auth/default_roles.py`），系统启动时 seed
B) **数据库种子**（Alembic data migration）
C) **JSON 配置文件**（`config/default_roles.json`）便于编辑
D) Other

[Answer]: A

---

## 第二部分：执行清单（待批准后执行）

### A. 业务实体与关系
- [ ] A1. 定义 7 个核心实体（tenant / user / role / permission / user_role / user_permission_override / refresh_token / audit_log）
- [ ] A2. 字段定义（含约束、默认值、枚举）
- [ ] A3. 关系图（ER 简图）
- [ ] A4. 写入 `aidlc-docs/construction/U01/functional-design/domain-entities.md`

### B. 业务规则
- [ ] B1. 密码策略（基于 Q1）
- [ ] B2. 登录失败处理（基于 Q2）
- [ ] B3. JWT 生命周期与失效场景（基于 Q3, Q4）
- [ ] B4. 首次登录强制改密（基于 Q5）
- [ ] B5. 权限合并算法（基于 Q6）
- [ ] B6. 多租户上下文规则（基于 Q7）
- [ ] B7. 审计日志保留（基于 Q8）
- [ ] B8. 备份范围与恢复（基于 Q9, Q10）
- [ ] B9. 写入 `aidlc-docs/construction/U01/functional-design/business-rules.md`

### C. 业务逻辑模型
- [ ] C1. 登录流程（成功/失败/限流）
- [ ] C2. 密码修改流程（含 token 失效）
- [ ] C3. 用户管理流程（创建/角色分配/启用禁用）
- [ ] C4. 权限计算流程（有效权限算法）
- [ ] C5. 审计日志查询流程
- [ ] C6. 多租户上下文注入流程
- [ ] C7. 备份与恢复流程
- [ ] C8. 写入 `aidlc-docs/construction/U01/functional-design/business-logic-model.md`

### D. 一致性校验
- [ ] D1. 所有 EP01-S01~S04, S07, S08 + NFR03 + NFR04 都被覆盖
- [ ] D2. 所有规则与 requirements.md 第 3.3、3.4、11.1、12.4 节一致
- [ ] D3. 与应用设计的 component-methods.md 第 6 节方法签名一致

### E. 状态更新
- [ ] E1. 更新 aidlc-state.md，标记 U01 Functional Design 完成
- [ ] E2. audit.md 记录完成时间戳
