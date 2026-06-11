"""U05 财务结款核心模块。

按 4 层架构（沿用 U01-U04）+ State Machine 独立子层 + Listeners 独立子层（双向事件）：
- enums: SettlementStatus / ExtraItemType
- models: Settlement / SettlementExtraItem / SettlementSequence ORM（**无 is_active 字段，FB3**）
- schemas: Pydantic
- state_machines: SettlementStatusMachine（5 状态 6 转移）
- events: SettlementPaid（required_handler=False，反向通知类）
- attachment_validator: ProofAttachmentValidator（6 项强校验，FB4）
- repository: 数据访问（含 next_settlement_sequence + update_state + daily_summary 双口径）
- domain: 业务规则验证 + audit 脱敏 + format_settlement_no
- service: 业务编排 + 事件分发（强一致正向 + 通知类反向）+ 失败 audit 脱敏
- listeners: on_settlement_requested 强一致正向 listener
- api: FastAPI Router（含 DELETE 405）

8 P1 反馈守护：完全继承 U04，详见 nfr-design-patterns.md。
"""
