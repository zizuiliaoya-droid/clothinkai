"""U05 finance 模块权限字符串常量。"""

from __future__ import annotations


SETTLEMENT_PERMISSIONS: list[tuple[str, str, str]] = [
    # (scope, action, description)
    ("settlement", "read", "查询结算单"),
    ("settlement", "write", "编辑结算单字段（不含付款截图上传）"),
    ("settlement.review", "approve", "PR 主管核查 / 驳回结算"),
    ("settlement.pay", "upload_proof", "财务上传付款截图 + mark_paid"),
    # U16 拍单/刷单/余额
    ("finance.order", "read", "查询拍单/刷单"),
    ("finance.order", "write", "录入拍单/刷单"),
    ("finance.balance", "read", "查询余额流水"),
    ("finance.balance", "write", "录入余额流水"),
]
