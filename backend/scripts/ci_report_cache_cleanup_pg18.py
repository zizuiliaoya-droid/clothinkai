"""复用既有PG18临时库：068↔069空缓存回滚及日表不变证据。"""

from __future__ import annotations

import json
from contextlib import closing
from uuid import uuid4

import psycopg2

from scripts.ci_flow7_pg18 import (
    ARTIFACTS,
    connect,
    current,
    environment,
    execute,
    migrate,
    scalar,
    verify_server,
)

PREVIOUS = "068_promotion_content_platform"
HEAD = "069_remove_unused_shop_caches"
CACHES = (("shop_week_summary", "week_start"), ("shop_month_summary", "period_month"))
DAILY = (
    "product_roi_summary",
    "pr_work_progress_summary",
    "shop_daily_summary",
    "report_summary_coverage",
)


def schema(conn, table: str) -> dict:
    """对比052+062实际schema，包括字段顺序/精度/默认值、约束、索引、策略和权限。"""
    queries = {
        "columns": """SELECT column_name, data_type, udt_name, is_nullable, numeric_precision,
            numeric_scale, datetime_precision, column_default FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""",
        "constraints": """SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid=to_regclass(%s) ORDER BY conname""",
        "indexes": """SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename=%s ORDER BY indexname""",
        "policies": """SELECT policyname, permissive, roles::text, cmd, qual, with_check
            FROM pg_policies WHERE schemaname='public' AND tablename=%s ORDER BY policyname""",
        "owner_rls": """SELECT pg_get_userbyid(relowner), relrowsecurity, relforcerowsecurity
            FROM pg_class WHERE oid=to_regclass(%s)""",
        "acl": """SELECT grantee, privilege_type, is_grantable FROM information_schema.role_table_grants
            WHERE table_schema='public' AND table_name=%s ORDER BY grantee, privilege_type""",
    }
    result = {}
    with conn.cursor() as cursor:
        for key, query in queries.items():
            cursor.execute(query, (table,))
            result[key] = cursor.fetchall()
    assert result["owner_rls"] == [("clothing_bypass", True, True)]
    assert result["policies"], table
    assert {row[1] for row in result["acl"] if row[0] == "clothing_app"} == {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
    }
    return result


def daily_snapshot(conn) -> dict:
    return {
        table: scalar(
            conn,
            f"SELECT md5(COALESCE(string_agg(to_jsonb(t)::text, '' ORDER BY id), '')) FROM {table} t",  # noqa: S608 固定表名
        )
        for table in DAILY
    }


def seed_cache(conn, table: str, bucket: str, tenant_id: str) -> None:
    execute(
        conn,
        f"INSERT INTO {table} (id,tenant_id,{bucket},pay_amount,refreshed_at) VALUES (%s,%s,'1970-01-05',19.90,now())",  # noqa: S608 固定表名/列名
        (str(uuid4()), tenant_id),
    )


def assert_absent(conn) -> None:
    assert current(conn) == HEAD
    for table, _ in CACHES:
        assert scalar(conn, "SELECT to_regclass(%s)", (table,)) is None


def main() -> None:
    values = environment()  # 既有白名单限定GitHub临时本机库，拒绝生产地址。
    with closing(connect(values["DATABASE_URL_SYNC"])) as owner:
        verify_server(owner)
        assert scalar(owner, "SELECT current_user") == "clothing_bypass"
        assert current(owner) == PREVIOUS
        before = {table: schema(owner, table) for table, _ in CACHES}
        daily = daily_snapshot(owner)
        tenant_id = str(scalar(owner, "SELECT id FROM tenant ORDER BY id LIMIT 1"))
        assert tenant_id != "None"
        for table, bucket in CACHES:
            seed_cache(owner, table, bucket, tenant_id)

        migrate(HEAD, "report-cleanup-upgrade-069")
        assert_absent(owner)
        assert daily_snapshot(owner) == daily

        migrate(PREVIOUS, "report-cleanup-downgrade-068", down=True)
        assert current(owner) == PREVIOUS
        for table, _ in CACHES:
            assert schema(owner, table) == before[table], table
            assert scalar(owner, f"SELECT count(*) FROM {table}") == 0  # noqa: S608 固定表名
        assert daily_snapshot(owner) == daily

        with closing(connect(values["TEST_DATABASE_URL_APP"])) as app:
            assert scalar(app, "SELECT current_user") == "clothing_app"
            execute(app, "SELECT set_config('app.bypass_rls','off',false)")
            for table, bucket in CACHES:
                execute(app, "SELECT set_config('app.tenant_id',%s,false)", (tenant_id,))
                seed_cache(app, table, bucket, tenant_id)
                assert scalar(app, f"SELECT count(*) FROM {table}") == 1  # noqa: S608 固定表名
                execute(app, "SELECT set_config('app.tenant_id',%s,false)", (str(uuid4()),))
                assert scalar(app, f"SELECT count(*) FROM {table}") == 0  # noqa: S608 固定表名
                try:
                    seed_cache(app, table, bucket, tenant_id)
                except psycopg2.errors.InsufficientPrivilege:
                    pass
                else:
                    raise AssertionError(f"{table}: RLS允许跨租户写入")

        migrate(HEAD, "report-cleanup-reupgrade-069")
        assert_absent(owner)
        assert daily_snapshot(owner) == daily

    result = {
        "passed": True,
        "revision": HEAD,
        "restored_schema": before,
        "cache_rows_restored": 0,
        "daily_and_coverage_unchanged": True,
        "app_rls_verified": True,
    }
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "report-cleanup-pg18-drill.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("068 -> 069 -> 068(empty cache/schema/RLS/ACL) -> 069: passed")


if __name__ == "__main__":
    main()
