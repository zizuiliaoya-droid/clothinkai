"""U04 推广合作核心模块。

按 4 层架构（沿用 U01-U03）+ State Machine 独立子层 + 事件驱动：
- enums: PublishStatus / RecallStatus / SettlementStatus / ReviewAction
- models: Promotion / PromotionSequence ORM
- schemas: Pydantic
- state_machines: 3 状态机（基于 U01 core/state_machine.py 基类）
- events: SettlementRequested / PromotionPublished
- urge_calculator / metrics_calculator: 衍生字段双实现
- repository: 数据访问（含 next_internal_sequence INSERT ON CONFLICT + update_state UPDATE WHERE old_state）
- domain: 业务规则验证 + audit 脱敏
- service: 业务编排 + 事件分发 + 失败 audit 脱敏
- api: FastAPI Router

8 P1 反馈守护：见 nfr-design-patterns.md。
"""
