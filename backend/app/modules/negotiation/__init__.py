"""谈款审核模块（PRD V1.4 模块一）。

PR 录入博主谈款信息，主管审核；审核通过自动生成推广管理单据。

为什么单独一个模块而不是挂在 promotion 下：

- ``promotion`` 的 ``reviewed_by`` / ``review_action`` / ``review_reason`` 已经被
  **结款审核**占用（发布后主管核查，驱动 settlement_status）。谈款审核是**建单之前**的
  另一道审核，方向相反，复用那组字段会让两种审核混在一起。
- 权限也必须分开：PR 持有 ``promotion.*:*``，如果谈款审核的 scope 挂在 ``promotion.``
  下，PR 会自动拿到审核权限（``has()`` 的前缀通配只看第一段）。所以用独立一级域
  ``negotiation``。
"""
