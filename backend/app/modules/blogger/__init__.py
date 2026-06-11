"""U03 博主库基础模块。

按 4 层架构（沿用 U01/U02）：
- enums: BloggerType / Platform / GenderTarget
- models: Blogger ORM
- schemas: Pydantic
- repository: 数据访问（含 search 防侧信道 + upsert_atomic）
- domain: 业务规则验证
- service: 业务编排（含 U10b 4 钩子占位）
- api: FastAPI Router
"""
