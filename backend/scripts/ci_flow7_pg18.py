"""仅GitHub临时PG18.6：真实owner迁移、M5合成存量往返与验收证据。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg2

DB = "clothing_erp_test_flow7_pg18"
PREVIOUS = "066_platform_metric_setting"
HEAD = "067_retro_completion"
ARTIFACTS = Path("ci-artifacts")


def validate_dsn(value: str, *, admin: bool = False) -> str:
    """拒绝任意非本机/非独占测试库地址；错误信息不包含输入。"""
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"postgresql", "postgresql+asyncpg"}
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != 5432
        or parsed.path != ("/postgres" if admin else f"/{DB}")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CI DSN 不符合本机独占测试库白名单")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def environment() -> dict[str, str]:
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("CI_FLOW7_PG18") != "1":
        raise ValueError("仅允许显式GitHub临时测试runner")
    keys = (
        "DATABASE_URL_APP",
        "DATABASE_URL_BYPASS",
        "DATABASE_URL_SYNC",
        "TEST_DATABASE_URL_APP",
        "TEST_DATABASE_URL_BYPASS",
    )
    values = {key: validate_dsn(os.environ[key]) for key in keys}
    values["CI_ADMIN_DATABASE_URL"] = validate_dsn(os.environ["CI_ADMIN_DATABASE_URL"], admin=True)
    if urlparse(values["DATABASE_URL_BYPASS"]).username != "clothing_bypass":
        raise ValueError("迁移必须使用真实clothing_bypass owner")
    return values


def connect(dsn: str):
    conn = psycopg2.connect(dsn, connect_timeout=10)
    conn.autocommit = True
    return conn


def scalar(conn, query: str, params=()):
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()[0]


def execute(conn, query: str, params=()) -> None:
    with conn.cursor() as cur:
        cur.execute(query, params)


def verify_server(conn) -> None:
    assert scalar(conn, "SHOW server_version_num") == "180006", "需要确切PG18.6"
    assert scalar(conn, "SELECT current_database()") == DB


def bootstrap(values: dict[str, str]) -> dict:
    with closing(connect(values["CI_ADMIN_DATABASE_URL"])) as conn:
        assert scalar(conn, "SHOW server_version_num") == "180006"
        assert not scalar(conn, "SELECT EXISTS(SELECT FROM pg_database WHERE datname=%s)", (DB,))
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('clothing_app','clothing_bypass','clothing_archiver')",
            )
            == 0
        )
        execute(
            conn,
            "CREATE ROLE clothing_app LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT PASSWORD 'ci-app-only'",
        )
        execute(
            conn,
            "CREATE ROLE clothing_bypass LOGIN NOSUPERUSER BYPASSRLS NOINHERIT PASSWORD 'ci-bypass-only'",
        )
        execute(
            conn,
            "CREATE ROLE clothing_archiver LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT PASSWORD 'ci-archiver-only'",
        )
        execute(conn, f"CREATE DATABASE {DB} OWNER clothing_bypass")
    with closing(connect(values["TEST_DATABASE_URL_BYPASS"])) as conn:
        verify_server(conn)
        execute(conn, "CREATE EXTENSION pg_trgm")
    return {
        "phase": "bootstrap",
        "passed": True,
        "server_version_num": 180006,
        "database": DB,
        "migration_owner": "clothing_bypass",
    }


def migrate(target: str, phase: str, *, down: bool = False) -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    with (ARTIFACTS / f"{phase}.log").open("w", encoding="utf-8") as log:
        subprocess.run(  # noqa: S603 仅sys.executable与固定迁移目标，非外部输入
            [sys.executable, "-m", "alembic", "downgrade" if down else "upgrade", target],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )


def current(conn) -> str:
    return scalar(conn, "SELECT version_num FROM alembic_version")


def check_head(conn, ids: dict[str, str]) -> dict:
    assert current(conn) == HEAD
    assert scalar(conn, "SELECT count(*) FROM promotion WHERE completed_at IS NOT NULL") == 2
    for key in ("normal", "recall"):
        assert scalar(
            conn, "SELECT completed_at IS NOT NULL FROM promotion WHERE id=%s", (ids[key],)
        )
    assert (
        scalar(
            conn,
            "SELECT count(*) FROM promotion WHERE retro_status='待确认' OR (publish_status='已发布' AND retro_status='未开始')",
        )
        == 0
    )
    assert scalar(conn, "SELECT count(*) FROM promotion") == 5008
    assert scalar(conn, "SELECT sum(quote_amount) FROM promotion") == 500800
    assert scalar(
        conn,
        "SELECT content='合成旧PR草稿' AND confirmed_at IS NULL FROM blogger_retrospective WHERE id=%s",
        (ids["draft"],),
    )
    assert (
        scalar(
            conn,
            "SELECT count(*) FROM permission WHERE scope IN ('promotion_retro:write','promotion.retro:write','promotion.retro:confirm')",
        )
        == 3
    )
    assert (
        scalar(
            conn,
            "SELECT string_agg(r.code,',') FROM role_permission rp JOIN role r ON r.id=rp.role_id JOIN permission p ON p.id=rp.permission_id WHERE p.scope='promotion_retro:write'",
        )
        == "pr_manager"
    )
    assert scalar(
        conn, "SELECT EXISTS(SELECT FROM pg_indexes WHERE indexname='idx_promotion_completed')"
    )
    check = scalar(
        conn,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='promotion'::regclass AND conname='ck_promotion_ck_promotion_retro_status'",
    )
    assert "待确认" not in check
    assert (
        scalar(conn, "SELECT tableowner FROM pg_tables WHERE tablename='promotion'")
        == "clothing_bypass"
    )
    return {
        "revision": HEAD,
        "promotion_rows": 5008,
        "completed": 2,
        "legacy_draft_preserved": True,
        "amount_sum": "500800.00",
        "owner": "clothing_bypass",
    }


def drill(values: dict[str, str]) -> dict:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    scripts = ScriptDirectory.from_config(cfg)
    heads = scripts.get_heads()
    assert len(heads) == 1 and HEAD in {r.revision for r in scripts.walk_revisions(head=heads[0])}
    ids = {
        key: str(uuid4())
        for key in (
            "style",
            "blogger",
            "user1",
            "user2",
            "draft",
            "pending",
            "unstarted",
            "normal",
            "recall",
            "no_metrics",
            "no_retro",
            "no_settlement",
            "no_cooperation",
        )
    }
    phases = []
    with closing(connect(values["DATABASE_URL_SYNC"])) as conn:
        verify_server(conn)
        assert scalar(conn, "SELECT current_user") == "clothing_bypass"
        assert scalar(
            conn,
            "SELECT NOT rolsuper AND rolbypassrls AND NOT rolinherit FROM pg_roles WHERE rolname=current_user",
        )
        assert current(conn) == PREVIOUS
        tid = str(scalar(conn, "SELECT id FROM tenant ORDER BY created_at LIMIT 1"))
        execute(
            conn,
            "INSERT INTO style(id,tenant_id,style_code,style_name,category,design_status) VALUES (%s,%s,'FLOW7DRILL','合成款','连衣裙','大货')",
            (ids["style"], tid),
        )
        execute(
            conn,
            "INSERT INTO blogger(id,tenant_id,xiaohongshu_id,nickname,platform) VALUES (%s,%s,'FLOW7DRILL','合成博主','小红书')",
            (ids["blogger"], tid),
        )
        for index in (1, 2):
            execute(
                conn,
                "INSERT INTO \"user\"(id,tenant_id,username,password_hash) VALUES (%s,%s,%s,'synthetic-only')",
                (ids[f"user{index}"], tid, f"flow7drill{index}"),
            )
        execute(
            conn,
            "INSERT INTO promotion(id,tenant_id,style_id,blogger_id,internal_code,style_code_snapshot,style_short_name_snapshot,platform,cooperation_date,quote_amount) SELECT gen_random_uuid(),%s,%s,%s,'F7BULK'||i,'FLOW7DRILL','FLOW7DRILL','小红书',CURRENT_DATE,100 FROM generate_series(1,5000) i",
            (tid, ids["style"], ids["blogger"]),
        )
        cases = {
            "pending": ("已发布", "已结款", "未召回", "待确认", True),
            "unstarted": ("已发布", "未核查", "未召回", "未开始", False),
            "normal": ("已发布", "已结款", "未召回", "已完成", True),
            "recall": ("已取消", "未核查", "召回成功", "未开始", False),
            "no_metrics": ("已发布", "已结款", "未召回", "已完成", False),
            "no_retro": ("已发布", "已结款", "未召回", "待复盘", True),
            "no_settlement": ("已发布", "已付款", "未召回", "已完成", True),
            "no_cooperation": ("已取消", "未核查", "未召回", "未开始", False),
        }
        for key, (pub, stl, recall, retro, metrics) in cases.items():
            execute(
                conn,
                "INSERT INTO promotion(id,tenant_id,style_id,blogger_id,internal_code,style_code_snapshot,style_short_name_snapshot,platform,cooperation_date,quote_amount,publish_status,settlement_status,recall_status,retro_status,metrics_recorded_at) VALUES (%s,%s,%s,%s,%s,'FLOW7DRILL','FLOW7DRILL','小红书',CURRENT_DATE,100,%s,%s,%s,%s,CASE WHEN %s THEN now() END)",
                (
                    ids[key],
                    tid,
                    ids["style"],
                    ids["blogger"],
                    f"F7-{key}",
                    pub,
                    stl,
                    recall,
                    retro,
                    metrics,
                ),
            )
        execute(
            conn,
            "INSERT INTO blogger_retrospective(id,tenant_id,blogger_id,promotion_id,content,created_by) VALUES (%s,%s,%s,%s,'合成旧PR草稿',%s)",
            (ids["draft"], tid, ids["blogger"], ids["pending"], ids["user1"]),
        )
        phases.append({"phase": "baseline066", "passed": True, "promotion_rows": 5008})
        migrate(HEAD, "upgrade1")
        phases.append({"phase": "upgrade1", "passed": True, **check_head(conn, ids)})
        for index, effect in ((1, "grant"), (2, "revoke")):
            execute(
                conn,
                "INSERT INTO user_permission_override(id,tenant_id,user_id,permission_id,effect) SELECT %s,%s,%s,id,%s FROM permission WHERE scope='promotion_retro:write'",
                (str(uuid4()), tid, ids[f"user{index}"], effect),
            )
        assert scalar(conn, "SELECT count(*) FROM user_permission_override") == 2
        migrate(PREVIOUS, "downgrade", down=True)
        assert current(conn) == PREVIOUS
        assert scalar(conn, "SELECT count(*) FROM user_permission_override") == 0
        assert (
            scalar(conn, "SELECT count(*) FROM permission WHERE scope='promotion_retro:write'") == 0
        )
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM information_schema.columns WHERE table_name='promotion' AND column_name='completed_at'",
            )
            == 0
        )
        check = scalar(
            conn,
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='promotion'::regclass AND conname='ck_promotion_ck_promotion_retro_status'",
        )
        assert "待确认" in check
        phases.append(
            {
                "phase": "downgrade",
                "passed": True,
                "revision": PREVIOUS,
                "personal_overrides_lost": 2,
                "old_status_not_restored": True,
            }
        )
        migrate(HEAD, "upgrade2")
        phases.append({"phase": "upgrade2", "passed": True, **check_head(conn, ids)})
    return {
        "evidence_type": "synthetic_migration_drill",
        "passed": True,
        "phases": phases,
        "head_sha": os.environ.get("GITHUB_SHA"),
        "server_version_num": 180006,
    }


def verify_results(values: dict[str, str]) -> dict:
    drill_report = json.loads((ARTIFACTS / "flow7-pg18-drill.json").read_text(encoding="utf-8"))
    assert drill_report["passed"] and {
        p["phase"] for p in drill_report["phases"] if p["passed"]
    } == {"baseline066", "upgrade1", "downgrade", "upgrade2"}
    faults = json.loads((ARTIFACTS / "flow7-faults.json").read_text(encoding="utf-8"))
    assert len(faults) == 14 and all(f["passed"] and f["source_restored"] for f in faults)
    cases = ET.parse(ARTIFACTS / "flow7-pg18-tests.xml").findall(".//testcase")  # noqa: S314 本job生成的pytest报告
    assert cases and all(
        not any(c.find(tag) is not None for tag in ("failure", "error", "skipped")) for c in cases
    )
    names = [c.attrib["name"] for c in cases]
    for required in (
        "test_last_steps_and_completed_gate_are_serialized",
        "test_completion_and_retro_isolate_real_app_tenants",
        "test_every_last_step_completes_once",
    ):
        assert any(name.startswith(required) for name in names), required
    with closing(connect(values["DATABASE_URL_SYNC"])) as conn:
        verify_server(conn)
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", "alembic")
        assert ScriptDirectory.from_config(cfg).get_heads() == [current(conn)]
    return {
        "evidence_type": "synthetic_migration_drill_and_tests",
        "passed": True,
        "tests": len(cases),
        "skipped": 0,
        "revision": HEAD,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("bootstrap", "drill", "verify-results"))
    parser.add_argument("--ci", required=True, action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    values = environment()
    result = {"bootstrap": bootstrap, "drill": drill, "verify-results": verify_results}[
        args.action
    ](values)
    ARTIFACTS.mkdir(exist_ok=True)
    output = args.output or ARTIFACTS / f"flow7-pg18-{args.action}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
