# 需求验证问题

请回答以下问题，帮助我更好地理解项目需求。请在每个问题的 [Answer]: 标签后填写字母选项。

---

## Question 1
本次开发的范围是什么？

A) 仅后端API（FastAPI + PostgreSQL + Redis + Celery）
B) 前后端完整开发（React + FastAPI全栈）
C) 仅后端API + 数据库设计（不含前端）
D) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question 2
本次交付的优先级范围是什么？

A) 仅P0核心功能（商品成本表、站外推广、催发系统+企微、财务结款、发文进度表、数据采集引擎）
B) P0 + P1重要功能（含设计制版、工作进度表、博主库、千牛数据导入、投产报表等）
C) P0 + P1 + P2一般功能（含拍单、余额核对、BI看板、Excel导入导出）
D) 全部功能（P0-P3，含AI决策建议）
E) Other (please describe after [Answer]: tag below)

[Answer]: D

## Question 3
数据采集引擎（爬虫）的实现深度是什么？

A) 完整实现：包含千牛、万相台、灰豚的自动登录和数据抓取
B) 框架搭建：定义接口和任务调度，但爬虫逻辑留空（后续填充）
C) 仅手动上传：暂不实现自动采集，只做Excel/CSV手动上传
D) Other (please describe after [Answer]: tag below)

[Answer]: D，自动数据采集服务 + RPA Worker + API 上传。系统对外提供自动数据同步能力，用户授权并配置目标平台账号后，由后台采集 Worker 定时登录千牛、万相台、灰豚等平台导出数据，再通过统一导入 API 上传。后端负责凭据加密存储、任务调度、导入批次、字段映射、校验入库、失败重试和审计日志。具体 RPA 工具作为内部实现细节，不暴露给用户。

## Question 4
企业微信集成的实现深度是什么？

A) 完整实现：包含催发消息发送、回调处理、降级策略
B) 接口对接：实现API调用逻辑，但需要用户提供企微应用配置后才能运行
C) 模拟模式：定义接口但不实际调用企微API，用日志模拟发送
D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 5
多租户功能在首次交付中是否需要？

A) 需要：从一开始就支持多租户隔离
B) 预留：代码结构支持多租户，但首次部署只有一个租户
C) 不需要：单租户模式，后续再考虑多租户
D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 6
认证和权限系统的实现范围？

A) 完整RBAC：预设角色 + 自定义权限 + 字段级权限控制
B) 基础RBAC：预设角色 + 模块级权限，暂不实现字段级权限
C) 简单认证：JWT登录 + 基本角色区分，权限后续完善
D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 7
测试策略是什么？

A) 完整测试：单元测试 + 集成测试 + API测试
B) 核心测试：关键业务逻辑的单元测试 + 主要API的集成测试
C) 基础测试：仅API端点的基本测试
D) 暂不写测试：先实现功能，测试后续补充
E) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 8
是否有现有的数据需要迁移？

A) 是：需要从 final.xlsx 导入历史数据作为初始数据
B) 否：系统从空数据开始，用户后续手动录入
C) 部分：需要导入商品成本表和博主库的基础数据
D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 9
部署环境的准备情况？

A) 已有Zeabur账号和域名，可以直接部署
B) 先本地Docker开发，部署方案后续处理
C) 需要同时提供本地开发环境和Zeabur部署配置
D) Other (please describe after [Answer]: tag below)

[Answer]: D，先推到GitHub上，Zeabur导入GitHub链接来部署，本地环境用来开发测试

## Question 10
开发文档中提到的"设计制版管理"流程（P1），是否需要在本次开发中包含？

A) 是：完整实现设计→制版→工艺→核价的全流程
B) 部分：仅实现数据模型和基础CRUD，状态流转后续完善
C) 否：本次不包含，专注P0功能
D) Other (please describe after [Answer]: tag below)

[Answer]: A
