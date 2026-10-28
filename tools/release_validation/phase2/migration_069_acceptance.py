"""Fixed 067→068→069 data/schema contract; pure, no database or filesystem access."""

from contracts import AFTER, BEFORE, digest, exact, require, sha

REMOVED = {"shop_week_summary", "shop_month_summary"}
ADDED = "promotion_platform_post"
DAILY = (
    "product_roi_summary",
    "pr_work_progress_summary",
    "shop_daily_summary",
    "report_summary_coverage",
)


def projection(rows):
    require(len(rows["2"]) == 1, "acceptance_head_count")
    head = rows["2"][0]["version_num"]
    relations = rows["4"]
    names = [r["relname"] for r in relations]
    require(len(set(names)) == len(names), "acceptance_relation_duplicate")
    require(set(DAILY) <= set(names), "acceptance_daily_tables_missing")
    if head == BEFORE:
        require(
            REMOVED <= set(names) and ADDED not in names, "acceptance_067_relations"
        )
        require(rows["content_column"] == [], "acceptance_067_content_column")
    else:
        require(head == AFTER, "acceptance_revision")
        require(
            not REMOVED.intersection(names) and ADDED in names,
            "acceptance_069_relations",
        )
    categories = []
    for row in rows["0"]:
        exact(row, ("id", "tenant_id", "expected_category"))
        categories.append(
            {"id": row["id"], "expected_category": row["expected_category"]}
        )
    require(
        len({r["id"] for r in categories}) == len(categories), "acceptance_duplicate_id"
    )
    # Preserve every collected old business/role/day-cache projection. Only the
    # new column and the explicitly added/dropped relations differ this time.
    stable = {k: v for k, v in rows.items() if k not in {"2", "4", "content_column"}}
    stable["4"] = [r for r in relations if r["relname"] not in REMOVED | {ADDED}]
    return {
        "head": head,
        "stable_sha256": digest(stable),
        "categories_sha256": digest(categories),
        "tables": sorted(names),
    }


def column(name, kind, length=None, nullable="YES", default=None):
    return {
        "column_name": name,
        "data_type": kind,
        "character_maximum_length": length,
        "is_nullable": nullable,
        "column_default": default,
    }


CONTENT_COLUMN = [column("content_category", "character varying", 8)]
PLATFORM_COLUMNS = [
    column("id", "uuid", nullable="NO"),
    column("tenant_id", "uuid", nullable="NO"),
    column("created_at", "timestamp with time zone", nullable="NO", default="now()"),
    column("updated_at", "timestamp with time zone", nullable="NO", default="now()"),
    column("promotion_id", "uuid", nullable="NO"),
    column("platform", "character varying", 16, "NO"),
    column("publish_url", "character varying", 512),
    column("actual_publish_date", "date"),
    column("like_count", "integer"),
    column("sort_order", "smallint", nullable="NO", default="0"),
    column("created_by", "uuid"),
]
PLATFORM_CONSTRAINTS = {
    "PRIMARY KEY (id)",
    "CHECK ((like_count >= 0))",
    "FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE RESTRICT",
    "FOREIGN KEY (promotion_id) REFERENCES promotion(id) ON DELETE RESTRICT",
    'FOREIGN KEY (created_by) REFERENCES "user"(id) ON DELETE SET NULL',
}
PLATFORM_INDEXES = {
    "CREATE UNIQUE INDEX pk_promotion_platform_post ON public.promotion_platform_post USING btree (id)",
    "CREATE UNIQUE INDEX uq_platform_post ON public.promotion_platform_post USING btree (tenant_id, promotion_id, platform)",
}
PLATFORM_ACCESS = {
    "owner": "clothing_bypass",
    "relrowsecurity": True,
    "relforcerowsecurity": True,
    **{f"app_{action}": True for action in ("select", "insert", "update", "delete")},
    **{f"app_{action}": False for action in ("truncate", "references", "trigger")},
}


def verify_schema_rows(rows, promotion_policy):
    require(rows["0"] == [{"version_num": AFTER}], "acceptance_target_head")
    require(rows["2"] == CONTENT_COLUMN, "acceptance_content_column")
    require(rows["3"] == PLATFORM_COLUMNS, "acceptance_platform_columns")
    require(
        len(rows["4"]) == len(PLATFORM_CONSTRAINTS)
        and {r["definition"] for r in rows["4"]} == PLATFORM_CONSTRAINTS,
        "acceptance_platform_constraints",
    )
    require(
        len(rows["5"]) == len(PLATFORM_INDEXES)
        and {r["indexdef"] for r in rows["5"]} == PLATFORM_INDEXES,
        "acceptance_platform_indexes",
    )
    require(rows["6"] == [PLATFORM_ACCESS], "acceptance_platform_access")
    require(
        len(promotion_policy) == 1
        and promotion_policy[0]["policyname"] == "tenant_isolation"
        and promotion_policy[0]["permissive"] == "PERMISSIVE"
        and promotion_policy[0]["roles"] == ["clothing_app"]
        and promotion_policy[0]["cmd"] == "ALL"
        and promotion_policy[0]["qual"]
        and promotion_policy[0]["qual"] == promotion_policy[0]["with_check"]
        and rows["7"] == promotion_policy,
        "acceptance_platform_policy",
    )
    require(rows["8"] == [{"platform_post_count": 0}], "acceptance_platform_rows")


def verify(before, after):
    exact(before, ("head", "stable_sha256", "categories_sha256", "tables"))
    exact(
        after,
        (
            "head",
            "stable_sha256",
            "categories_sha256",
            "tables",
            "schema_verified",
            "started_tick",
            "observed_tick",
            "gate_closed",
            "business_sessions",
            "old_processes",
        ),
    )
    require(before["head"] == BEFORE and after["head"] == AFTER, "acceptance_revision")
    for key in ("stable_sha256", "categories_sha256"):
        sha(before[key])
        require(before[key] == after[key], "acceptance_069_" + key)
    require(
        set(after["tables"]) == (set(before["tables"]) - REMOVED) | {ADDED},
        "acceptance_relation_delta",
    )
    require(
        after["schema_verified"] is True and after["gate_closed"] is True,
        "schema_or_gate",
    )
    require(
        type(after["business_sessions"]) is int
        and after["business_sessions"] == 0
        and type(after["old_processes"]) is int
        and after["old_processes"] == 0,
        "writers_alive",
    )
    require(
        type(after["started_tick"]) is int
        and type(after["observed_tick"]) is int
        and 0 <= after["started_tick"] <= after["observed_tick"],
        "migration_interval",
    )
    return {
        "mode": "validation_only",
        "head": AFTER,
        "acceptance_sha256": digest(after),
    }
