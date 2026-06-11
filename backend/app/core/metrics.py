"""自定义 Prometheus 指标。

U02 引入：
- ``style_search_results_count`` — 模糊匹配候选数分布（监控零候选率）
- ``sku_upsert_total`` — upsert 调用统计（按 created/updated 分类）

U04 引入：
- ``promotion_state_transitions_total`` — 状态机转移计数（按 status_field + from/to）
- ``settlement_requested_events_total`` — 事件总线 dispatch 结果（含 missing_handler）
- ``promotion_sequence_lock_duration_seconds`` — 序列号 INSERT ON CONFLICT 耗时
- ``promotion_search_results_count`` — 推广列表返回结果数分布

U05 引入：
- ``settlement_state_transitions_total`` — 状态机转移计数
- ``settlement_created_via_event_total`` — 事件创建结果（含 duplicate_skipped FB1+FB3+FB6）
- ``settlement_sequence_lock_duration_seconds`` — 序列号锁等待
- ``attachment_validation_failures_total`` — Attachment 6 项校验失败分布（FB4 含 6 类 failure_type）
- ``settlement_paid_sync_no_match_total`` — 反向事件 0 行匹配监控（FB5）

通用 HTTP / 业务指标由 ``prometheus-fastapi-instrumentator`` 自动暴露
（在 ``main.py`` 中初始化），本文件仅声明需要业务代码主动 record 的自定义指标。

使用方式::

    from app.core.metrics import sku_upsert_total, style_search_results_count

    style_search_results_count.observe(len(candidates))
    sku_upsert_total.labels(result="created").inc()
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# U02 product 模块指标
# ---------------------------------------------------------------------------


style_search_results_count: Histogram = Histogram(
    "style_search_results_count",
    "Distribution of search result counts (per /api/styles/match call)",
    buckets=(0, 1, 5, 10, 20),
)


sku_upsert_total: Counter = Counter(
    "sku_upsert_total",
    "Total upsert calls (categorized by result)",
    labelnames=("result",),
)


# ---------------------------------------------------------------------------
# U03 blogger 模块指标
# ---------------------------------------------------------------------------


blogger_search_results_count: Histogram = Histogram(
    "blogger_search_results_count",
    "Distribution of blogger search result counts (per /api/bloggers/ list call)",
    buckets=(0, 1, 5, 20, 100),
)


# ---------------------------------------------------------------------------
# U04 promotion 模块指标
# ---------------------------------------------------------------------------


promotion_state_transitions_total: Counter = Counter(
    "promotion_state_transitions_total",
    "Total promotion state machine transitions (by state field + transition direction)",
    labelnames=("from_state", "to_state", "status_field"),
)


settlement_requested_events_total: Counter = Counter(
    "settlement_requested_events_total",
    "Total SettlementRequested / event-bus events dispatch outcomes",
    labelnames=("result",),  # dispatched / handler_failed / no_handler / missing_handler
)


promotion_sequence_lock_duration_seconds: Histogram = Histogram(
    "promotion_sequence_lock_duration_seconds",
    "Duration of promotion_sequence INSERT ON CONFLICT operation",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)


promotion_search_results_count: Histogram = Histogram(
    "promotion_search_results_count",
    "Distribution of promotion list result counts (per /api/promotions/ list call)",
    buckets=(0, 1, 10, 100, 1000),
)


# ---------------------------------------------------------------------------
# U05 finance 模块指标
# ---------------------------------------------------------------------------


settlement_state_transitions_total: Counter = Counter(
    "settlement_state_transitions_total",
    "Total settlement state machine transitions",
    labelnames=("from_state", "to_state"),
)


settlement_created_via_event_total: Counter = Counter(
    "settlement_created_via_event_total",
    "Total settlement creation outcomes via SettlementRequested handler "
    "(FB1+FB3+FB6 三重幂等)",
    labelnames=("result",),  # created / duplicate_skipped / error
)


settlement_sequence_lock_duration_seconds: Histogram = Histogram(
    "settlement_sequence_lock_duration_seconds",
    "Duration of settlement_sequence INSERT ON CONFLICT operation",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)


attachment_validation_failures_total: Counter = Counter(
    "attachment_validation_failures_total",
    "Total attachment 6 strict validation failures (FB4)",
    labelnames=("failure_type", "source_module"),
    # failure_type ∈ {not_found, tenant_mismatch, bucket_invalid, purpose_invalid,
    #                 mime_invalid, size_too_large, status_not_ready}
)


settlement_paid_sync_no_match_total: Counter = Counter(
    "settlement_paid_sync_no_match_total",
    "Total times U04 listener UPDATE promotion.settlement_status returned 0 rows "
    "(FB5 反向同步丢失监控)",
)


# ---------------------------------------------------------------------------
# U06a importer 模块指标
# ---------------------------------------------------------------------------


import_batch_total: Counter = Counter(
    "import_batch_total",
    "Total import batch completions (by source + terminal status)",
    labelnames=("source", "status"),  # status ∈ completed/partial/failed
)


import_rows_total: Counter = Counter(
    "import_rows_total",
    "Total import row-level outcomes",
    labelnames=("source", "result"),  # result ∈ success/failed
)


import_batch_duration_seconds: Histogram = Histogram(
    "import_batch_duration_seconds",
    "Duration of run_import_batch end-to-end row processing",
    labelnames=("source",),
    buckets=(0.5, 1.0, 5.0, 30.0, 60.0, 300.0),
)


import_file_size_bytes: Histogram = Histogram(
    "import_file_size_bytes",
    "Distribution of uploaded import file sizes",
    labelnames=("source",),
    buckets=(1e3, 1e4, 1e5, 1e6, 5e6, 1e7, 2e7),
)


import_retry_total: Counter = Counter(
    "import_retry_total",
    "Total import batch retry triggers",
    labelnames=("source",),
)


# ---------------------------------------------------------------------------
# U07 企微集成指标
# ---------------------------------------------------------------------------


wecom_message_total: Counter = Counter(
    "wecom_message_total",
    "Total wecom message terminal outcomes",
    labelnames=("status",),  # created/sent/rejected/rate_limited/failed
)


wecom_send_duration_seconds: Histogram = Histogram(
    "wecom_send_duration_seconds",
    "Duration of wecom add_msg_template API call",
)


wecom_rate_limited_total: Counter = Counter(
    "wecom_rate_limited_total",
    "Total wecom rate-limit degradations (by reason)",
    labelnames=("reason",),  # blogger/pr/api
)


wecom_callback_total: Counter = Counter(
    "wecom_callback_total",
    "Total wecom callback results",
    labelnames=("result",),  # sent/rejected/failed/invalid_signature/ignored
)


wecom_group_notify_total: Counter = Counter(
    "wecom_group_notify_total",
    "Total wecom group-robot control-comment notifications (U15)",
    labelnames=("status",),  # sent/failed/unconfigured/skipped
)


wecom_anomaly_alert_total: Counter = Counter(
    "wecom_anomaly_alert_total",
    "Total wecom anomaly alerts pushed to management group (U15)",
    labelnames=("alert_type", "status"),  # status: sent/failed/no_recipient/deduped
)


order_adjustment_auto_created_total: Counter = Counter(
    "order_adjustment_auto_created_total",
    "Total auto-created store orders from promotion (U16)",
    labelnames=("result",),  # created/skipped/failed
)


report_export_total: Counter = Counter(
    "report_export_total",
    "Total report exports (U17)",
    labelnames=("report_type", "result"),  # success/forbidden/invalid
)


ai_advice_total: Counter = Counter(
    "ai_advice_total",
    "Total AI advice requests (U18)",
    labelnames=("advice_type", "status"),  # success/degraded/failed
)


ai_advice_latency_seconds: Histogram = Histogram(
    "ai_advice_latency_seconds",
    "AI advice call latency in seconds (U18)",
)


# ---------------------------------------------------------------------------
# U12 凭据模块指标
# ---------------------------------------------------------------------------


credential_decrypt_total: Counter = Counter(
    "credential_decrypt_total",
    "Total credential decrypt operations (by platform + result)",
    labelnames=("platform", "result"),  # success / failed
)


credential_auto_paused_total: Counter = Counter(
    "credential_auto_paused_total",
    "Total credentials auto-paused due to consecutive collection failures",
    labelnames=("platform",),
)


# ---------------------------------------------------------------------------
# U13 采集模块指标
# ---------------------------------------------------------------------------


crawler_task_total: Counter = Counter(
    "crawler_task_total",
    "Total crawler task terminal outcomes (by platform + status)",
    labelnames=("platform", "status"),  # success / failed
)


crawler_poll_total: Counter = Counter(
    "crawler_poll_total",
    "Total crawler poll outcomes",
    labelnames=("result",),  # assigned / empty / auth_failed
)


worker_token_auth_failures_total: Counter = Counter(
    "worker_token_auth_failures_total",
    "Total worker token authentication failures",
)


data_quality_issue_total: Counter = Counter(
    "data_quality_issue_total",
    "Total data quality issues recorded (by source + severity)",
    labelnames=("source", "severity"),
)


# ---------------------------------------------------------------------------
# U14 报表进阶指标
# ---------------------------------------------------------------------------


report_query_duration_seconds: Histogram = Histogram(
    "report_query_duration_seconds",
    "Duration of advanced report aggregation queries",
    labelnames=("report_type",),  # work_progress/target/store_daily/production
    buckets=(0.05, 0.1, 0.3, 0.5, 0.8, 2.0),
)


__all__ = [
    "attachment_validation_failures_total",
    "blogger_search_results_count",
    "crawler_poll_total",
    "crawler_task_total",
    "credential_auto_paused_total",
    "credential_decrypt_total",
    "data_quality_issue_total",
    "import_batch_duration_seconds",
    "import_batch_total",
    "import_file_size_bytes",
    "import_retry_total",
    "import_rows_total",
    "promotion_search_results_count",
    "promotion_sequence_lock_duration_seconds",
    "promotion_state_transitions_total",
    "report_query_duration_seconds",
    "settlement_created_via_event_total",
    "settlement_paid_sync_no_match_total",
    "settlement_requested_events_total",
    "settlement_sequence_lock_duration_seconds",
    "settlement_state_transitions_total",
    "sku_upsert_total",
    "style_search_results_count",
    "wecom_callback_total",
    "wecom_message_total",
    "wecom_rate_limited_total",
    "wecom_send_duration_seconds",
    "wecom_group_notify_total",
    "wecom_anomaly_alert_total",
    "order_adjustment_auto_created_total",
    "report_export_total",
    "ai_advice_total",
    "ai_advice_latency_seconds",
    "worker_token_auth_failures_total",
]
