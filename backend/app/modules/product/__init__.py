"""U02 商品 / SKU 模块。

按 4 层架构：
- enums: Category / Season / Gender / DesignStatus / SourcingType
- models: Style / Sku / Brand / StyleDetailImage ORM
- schemas: Pydantic 请求/响应模型
- repository: 数据访问层
- domain: 业务规则验证
- service: 业务编排
- api: FastAPI Router
"""
