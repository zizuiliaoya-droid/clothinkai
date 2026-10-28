"""One fresh disposable PG18 run of the current release acceptance queries."""

# ruff: noqa: E402 -- standalone runner binds sibling tools before importing them
import copy
import json
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path.cwd()))

from contracts import AFTER, BEFORE, Rejected
from host_acceptance_control import (
    EXTRA,
    SQL_PINS,
    _after_projection,
    _roles,
    _statements,
)
from migration_069_acceptance import projection, verify
from scripts.ci_flow7_pg18 import (
    bootstrap,
    connect,
    environment,
    execute,
    migrate,
    scalar,
    verify_server,
)

SPEC = type(
    "Spec", (), {"_tools": tuple((n, (ROOT / n).read_bytes()) for n in SQL_PINS)}
)()


def rows(conn, query):
    return scalar(
        conn, "SELECT COALESCE(json_agg(r),'[]'::json) FROM (" + query + ") r"
    )


def queries(conn, name):
    return {str(i): rows(conn, q) for i, q in enumerate(_statements(SPEC, name))}


def baseline(conn):
    return {
        **queries(conn, "baseline_queries.sql"),
        **{k: rows(conn, q) for k, q in EXTRA.items()},
    }


def seed(conn):
    """Two tenants, old completed/retro/override data, and populated daily caches."""
    pairs = []
    for tenant_index in range(2):
        tid, uid, sid, gid = (str(uuid4()) for _ in range(4))
        execute(
            conn,
            "INSERT INTO tenant(id,code,name,status) VALUES (%s,%s,'合成租户','active')",
            (tid, f"release069-{tenant_index}"),
        )
        execute(
            conn,
            'INSERT INTO "user"(id,tenant_id,username,password_hash) VALUES (%s,%s,%s,%s)',
            (uid, tid, f"release069-{tenant_index}", "synthetic-only"),
        )
        execute(
            conn,
            "INSERT INTO style(id,tenant_id,style_code,style_name,category,design_status) VALUES (%s,%s,'RELEASE069','合成款','连衣裙','大货')",
            (sid, tid),
        )
        execute(
            conn,
            "INSERT INTO goods_main(id,tenant_id,goods_code,goods_title) VALUES (%s,%s,'RELEASE069','合成商品')",
            (gid, tid),
        )
        for index, category in enumerate((None, "单篇", "合集", "历史分类")):
            bid, pid = str(uuid4()), str(uuid4())
            execute(
                conn,
                "INSERT INTO blogger(id,tenant_id,xiaohongshu_id,nickname,platform,content_category) VALUES (%s,%s,%s,'合成博主','小红书',%s)",
                (bid, tid, f"R069-{index}", category),
            )
            execute(
                conn,
                "INSERT INTO promotion(id,tenant_id,style_id,blogger_id,internal_code,style_code_snapshot,style_short_name_snapshot,platform,cooperation_date,quote_amount,publish_status,settlement_status,retro_status,completed_at) VALUES (%s,%s,%s,%s,%s,'RELEASE069','合成款','小红书',CURRENT_DATE,100,'已发布','已结款','已完成',now())",
                (pid, tid, sid, bid, f"R069-{index}"),
            )
            execute(
                conn,
                "INSERT INTO blogger_retrospective(id,tenant_id,blogger_id,promotion_id,content,created_by) VALUES (%s,%s,%s,%s,'现有复盘保留',%s)",
                (str(uuid4()), tid, bid, pid, uid),
            )
            if index == 0:
                pairs.append((tid, pid))
        execute(
            conn,
            "INSERT INTO user_permission_override(id,tenant_id,user_id,permission_id,effect) SELECT %s,%s,%s,id,%s FROM permission WHERE scope='promotion_retro:write'",
            (str(uuid4()), tid, uid, "grant" if tenant_index == 0 else "revoke"),
        )
        for table, bucket in (
            ("shop_daily_summary", "stat_date"),
            ("shop_week_summary", "week_start"),
            ("shop_month_summary", "period_month"),
        ):
            execute(
                conn,
                f"INSERT INTO {table}(id,tenant_id,{bucket},refreshed_at,pay_amount) VALUES (%s,%s,CURRENT_DATE,now(),321)",
                (str(uuid4()), tid),
            )
        execute(
            conn,
            "INSERT INTO pr_work_progress_summary(id,tenant_id,pr_id,stat_date,refreshed_at,quote_count) VALUES (%s,%s,%s,CURRENT_DATE,now(),7)",
            (str(uuid4()), tid, uid),
        )
        execute(
            conn,
            "INSERT INTO product_roi_summary(id,tenant_id,goods_main_id,stat_date,refreshed_at,pay_amount) VALUES (%s,%s,%s,CURRENT_DATE,now(),123)",
            (str(uuid4()), tid, gid),
        )
        execute(
            conn,
            "INSERT INTO report_summary_coverage(id,tenant_id,stat_date,refreshed_at) VALUES (%s,%s,CURRENT_DATE,now())",
            (str(uuid4()), tid),
        )
        execute(
            conn,
            "INSERT INTO store_daily(id,tenant_id,date,refund_only_amount,remark) VALUES (%s,%s,CURRENT_DATE,12.34,'保留手填值')",
            (str(uuid4()), tid),
        )
    return pairs


def rls_probe(conn, app_dsn, pairs):
    for tid, pid in pairs:
        execute(
            conn,
            "INSERT INTO promotion_platform_post(id,tenant_id,promotion_id,platform) VALUES (%s,%s,%s,'抖音')",
            (str(uuid4()), tid, pid),
        )
    for tid, pid in pairs:
        with closing(connect(app_dsn)) as app:
            assert scalar(
                app,
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user",
            )
            assert scalar(app, "SELECT count(*) FROM promotion_platform_post") == 0
            execute(app, "SELECT set_config('app.tenant_id',%s,false)", (tid,))
            assert scalar(app, "SELECT count(*) FROM promotion_platform_post") == 1
            assert (
                scalar(app, "SELECT promotion_id::text FROM promotion_platform_post")
                == pid
            )
            other = pairs[1] if pairs[0][0] == tid else pairs[0]
            assert (
                scalar(
                    app,
                    "WITH updated AS (UPDATE promotion_platform_post SET like_count=9 WHERE tenant_id=%s RETURNING id) SELECT count(*) FROM updated",
                    (other[0],),
                )
                == 0
            )
            try:
                execute(
                    app,
                    "INSERT INTO promotion_platform_post(id,tenant_id,promotion_id,platform) VALUES (%s,%s,%s,'快手')",
                    (str(uuid4()), other[0], other[1]),
                )
            except Exception as exc:
                assert getattr(exc, "pgcode", None) == "42501"
            else:
                raise AssertionError("cross_tenant_insert_allowed")


def main():
    values = environment()
    bootstrap(values)
    migrate(BEFORE, "release069-baseline067")
    with closing(connect(values["DATABASE_URL_SYNC"])) as conn:
        verify_server(conn)
        assert scalar(conn, "SELECT current_user") == "clothing_bypass"
        pairs = seed(conn)
        before_rows = baseline(conn)
        before = {"rows": before_rows, "projection": projection(before_rows)}
        window = {
            "db_before_utc": datetime.now(timezone.utc).isoformat(),
            "started_monotonic_ns": time.monotonic_ns(),
        }
        migrate(AFTER, "release069-upgrade")
        window.update(
            db_after_utc=datetime.now(timezone.utc).isoformat(),
            completed_monotonic_ns=time.monotonic_ns(),
        )
        after_rows = baseline(conn)
        schema_rows, counts = (
            queries(conn, "migration_acceptance.sql"),
            queries(conn, "acceptance_queries.sql"),
        )
        candidate = _after_projection(
            before, after_rows, schema_rows, counts, window, {"gate_closed": True}
        )
        result = verify(before["projection"], candidate)
        assert _roles(before_rows, after_rows)[0] == _roles(before_rows, after_rows)[1]
        # These represent real failures the release must not accidentally accept.
        for field in ("promotions", "overrides", "shop_daily_summary", "store_daily"):
            bad = copy.deepcopy(after_rows)
            bad[field] = []
            try:
                verify(before["projection"], {**candidate, **projection(bad)})
            except Rejected as exc:
                assert str(exc) == "acceptance_069_stable_sha256"
            else:
                raise AssertionError("changed_old_data_accepted:" + field)
        bad = copy.deepcopy(schema_rows)
        bad["6"][0]["app_truncate"] = True
        try:
            _after_projection(
                before, after_rows, bad, counts, window, {"gate_closed": True}
            )
        except Rejected as exc:
            assert str(exc) == "acceptance_platform_access"
        else:
            raise AssertionError("excess_platform_acl_accepted")
        rls_probe(conn, values["DATABASE_URL_APP"], pairs)
    result.update(
        passed=True,
        server_version_num=180006,
        owner="clothing_bypass",
        category_cases=4,
        populated_tenants=2,
        preserved_tables=5,
        negative_contracts=5,
        app_rls_select_and_write_verified=True,
        production_access=False,
    )
    Path("ci-artifacts").mkdir(exist_ok=True)
    Path("ci-artifacts/release-069-result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
