"""既有临时PG18通道中的PR9合成回填、往返及必需测试证据。"""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from contextlib import closing
from uuid import uuid4

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

PREVIOUS = "067_retro_completion"
HEAD = "068_promotion_content_platform"


def drill(values):
    phases = []
    with closing(connect(values["DATABASE_URL_SYNC"])) as conn:
        verify_server(conn)
        assert current(conn) == PREVIOUS
        assert scalar(conn, "SELECT current_user") == "clothing_bypass"
        tid = scalar(conn, "SELECT id FROM tenant ORDER BY created_at LIMIT 1")
        style = str(uuid4())
        execute(
            conn,
            "INSERT INTO style(id,tenant_id,style_code,style_name,category,design_status) VALUES (%s,%s,'FLOW9DRILL','合成款','连衣裙','大货')",
            (style, tid),
        )
        ids = []
        for index, category in enumerate((None, "单篇", "合集", "历史分类")):
            blogger, promo = str(uuid4()), str(uuid4())
            execute(
                conn,
                "INSERT INTO blogger(id,tenant_id,xiaohongshu_id,nickname,platform,content_category) VALUES (%s,%s,%s,'合成博主','小红书',%s)",
                (blogger, tid, f"FLOW9-{index}", category),
            )
            execute(
                conn,
                "INSERT INTO promotion(id,tenant_id,style_id,blogger_id,internal_code,style_code_snapshot,style_short_name_snapshot,platform,cooperation_date,quote_amount,source_extra) VALUES (%s,%s,%s,%s,%s,'FLOW9DRILL','合成款','小红书',CURRENT_DATE,100,%s::jsonb)",
                (promo, tid, style, blogger, f"F9DRILL-{index}", '{"合作形式":"线下"}'),
            )
            ids.append((promo, category))
        phases.append({"phase": "baseline067", "passed": True})
        for step in (1, 2):
            migrate(HEAD, f"flow9-upgrade{step}")
            assert current(conn) == HEAD
            for promo, category in ids:
                assert (
                    scalar(conn, "SELECT content_category FROM promotion WHERE id=%s", (promo,))
                    == category
                )
                assert (
                    scalar(
                        conn,
                        "SELECT source_extra->>'合作形式' FROM promotion WHERE id=%s",
                        (promo,),
                    )
                    == "线下"
                )
            assert (
                scalar(
                    conn,
                    "SELECT tableowner FROM pg_tables WHERE tablename='promotion_platform_post'",
                )
                == "clothing_bypass"
            )
            assert scalar(
                conn,
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='promotion_platform_post'::regclass",
            )
            assert scalar(
                conn,
                "SELECT has_table_privilege('clothing_app','promotion_platform_post','SELECT,INSERT,UPDATE,DELETE')",
            )
            assert (
                scalar(
                    conn,
                    "SELECT count(*) FROM pg_policies WHERE tablename='promotion_platform_post' AND 'clothing_app'=ANY(roles)",
                )
                == 1
            )
            phases.append(
                {
                    "phase": f"upgrade{step}",
                    "revision": HEAD,
                    "owner": "clothing_bypass",
                    "rls_acl": True,
                    "backfill_cases": 4,
                    "passed": True,
                }
            )
            if step == 1:
                migrate(PREVIOUS, "flow9-downgrade", down=True)
                assert current(conn) == PREVIOUS
                assert scalar(conn, "SELECT to_regclass('promotion_platform_post')") is None
                assert (
                    scalar(
                        conn,
                        "SELECT count(*) FROM information_schema.columns WHERE table_name='promotion' AND column_name='content_category'",
                    )
                    == 0
                )
                phases.append({"phase": "downgrade", "revision": PREVIOUS, "passed": True})
    return {
        "passed": True,
        "head_sha": os.environ.get("GITHUB_SHA"),
        "server_version_num": 180006,
        "phases": phases,
    }


def verify(values):
    report = json.loads((ARTIFACTS / "flow9-pg18-drill.json").read_text())
    assert report["passed"] and {p["phase"] for p in report["phases"] if p["passed"]} == {
        "baseline067",
        "upgrade1",
        "downgrade",
        "upgrade2",
    }
    cases = ET.parse(ARTIFACTS / "flow9-pg18-tests.xml").findall(".//testcase")  # noqa: S314 own pytest output
    assert cases and all(
        not any(c.find(tag) is not None for tag in ("failure", "error", "skipped")) for c in cases
    )
    names = {c.attrib["name"] for c in cases}
    for name in (
        "test_created_snapshot_is_independent",
        "test_platform_replace_is_atomic_and_primary_remains_authoritative",
        "test_completed_posts_only_admin_can_edit",
        "test_platform_posts_isolate_real_app_tenants",
        "test_copied_to_promotion",
    ):
        assert name in names, name
    faults = json.loads((ARTIFACTS / "flow9-faults.json").read_text())
    assert len(faults) == 3 and all(f["passed"] and f["source_restored"] for f in faults)
    with closing(connect(values["DATABASE_URL_SYNC"])) as conn:
        verify_server(conn)
        assert current(conn) == HEAD
    return {
        "passed": True,
        "revision": HEAD,
        "tests": len(cases),
        "skipped": 0,
        "faults": len(faults),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("drill", "verify"))
    args = parser.parse_args()
    values = environment()
    result = drill(values) if args.action == "drill" else verify(values)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / f"flow9-pg18-{'drill' if args.action == 'drill' else 'result'}.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
