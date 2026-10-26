"""Finite original-host backup and isolated restore; no retry or cleanup path.

Only durable host intents and externally provisioned authority reach execution.
Tests replace private OS boundaries, never certify a real backup or PG restore.
"""

import asyncio
import hashlib
import os
import re
import stat
import time
from pathlib import Path

import backup_adapter
import restore_drill
from contracts import BEFORE, canonical, digest, exact, require, sha
from fixed_host_transport import DOCKER, ROLES, ROOT, _capture, collect, command
from host_release_input import require_step
from host_session_contract import two_idle_windows
from host_timer_control import manual_freeze, observe_timer
from sql_policy import parse, require_host_authority

PG_IMAGE = "sha256:71620a0f5bece2005b364990f04be6c05d852796413a18716508fa101f8f7a8d"
DB = "clothing_erp_prod_20261018"
BACKUP_SHA = "082ad3f1dbee2911ee325e551c4a0d59faee752bedf7d0c69ae35b59172a49d6"
UPLOAD_SHA = "a9f5b74c46351bd57b854002f75f2a4d6f6fc49c499e3ec9f11795ad9bb1ff24"
NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}\.dump(?:\.json)?\Z")
ROLE_SQL = b"""CREATE ROLE clothing_app NOSUPERUSER NOINHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1;
CREATE ROLE clothing_bypass NOSUPERUSER NOINHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION BYPASSRLS CONNECTION LIMIT -1;
CREATE ROLE clothing_archiver NOSUPERUSER NOINHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1;
"""
MOUNT_FORMAT = (
    '{"id":{{json .Id}},"image":{{json .Image}},'
    '"network":{{json .HostConfig.NetworkMode}},'
    '"privileged":{{json .HostConfig.Privileged}},'
    '"cap_add":{{json .HostConfig.CapAdd}},"cap_drop":{{json .HostConfig.CapDrop}},'
    '"ports":{{json .HostConfig.PortBindings}},'
    '"tmpfs":{{json (index .HostConfig "Tmpfs")}},"mounts":['
    "{{$first := true}}{{range .Mounts}}{{if not $first}},{{end}}"
    '{"source":{{json .Source}},"destination":{{json .Destination}},'
    '"rw":{{json .RW}},"type":{{json .Type}}}{{$first = false}}{{end}}]}'
)


def _directory(path):
    """Open every fixed absolute path component without following symlinks."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "backup_directory_path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(
                info.st_uid == 0 and not info.st_mode & 0o022, "backup_directory_owner"
            )
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd, name, limit):
    file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        info = os.fstat(file)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_nlink == 1
            and not info.st_mode & 0o077
            and 0 < info.st_size <= limit,
            "backup_file_metadata",
        )
        chunks, size = [], 0
        while chunk := os.read(file, min(65536, limit + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
            require(size <= limit, "backup_file_limit")
        end = os.fstat(file)
        require(
            (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns)
            and size == info.st_size,
            "backup_file_changed",
        )
        return b"".join(chunks)
    finally:
        os.close(file)


def _script_pins():
    fd = _directory(ROOT + "/build/control")
    try:
        # Public scripts may be 0644; unlike private receipts they need not be 0600.
        for name, expected in (
            ("backup.py", BACKUP_SHA),
            ("upload_backup.py", UPLOAD_SHA),
        ):
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                info = os.fstat(handle)
                require(
                    stat.S_ISREG(info.st_mode)
                    and info.st_uid == 0
                    and info.st_nlink == 1
                    and not info.st_mode & 0o022
                    and info.st_size <= 32768,
                    "backup_script_metadata",
                )
                raw = os.read(handle, 32769)
                end = os.fstat(handle)
                require(
                    len(raw) == info.st_size
                    and hashlib.sha256(raw).hexdigest() == expected
                    and (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    == (end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns),
                    "backup_script_pin",
                )
            finally:
                os.close(handle)
    finally:
        os.close(fd)


def _snapshot():
    """Bounded full reads, including old files; lock inode is checked, not acquired."""
    deadline = time.monotonic() + 600
    fd = _directory(ROOT + "/backups")
    try:
        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "backup_directory_private")
        names = sorted(os.listdir(fd))
        require(len(names) <= 10000, "backup_file_count")
        require("backup.lock" in names, "backup_original_lock_missing")
        rows, total = {}, 0
        for name in names:
            require(
                name == "backup.lock" or NAME.fullmatch(name), "backup_unexpected_file"
            )
            file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                info = os.fstat(file)
                require(
                    stat.S_ISREG(info.st_mode)
                    and info.st_uid == 0
                    and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600,
                    "backup_file_metadata",
                )
                if name == "backup.lock":
                    require(info.st_size == 0, "backup_lock_metadata")
                    rows[name] = {
                        "bytes": 0,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                        "mtime_ns": 0,
                        "ctime_ns": 0,
                        "inode": info.st_ino,
                        "device": info.st_dev,
                    }
                    continue
                require(0 < info.st_size <= backup_adapter.MAX_DUMP, "backup_size")
                total += info.st_size
                require(total < 10 * 1024**3, "backup_quota")
                with os.fdopen(os.dup(file), "rb") as stream:
                    hashed, count = hashlib.sha256(), 0
                    while block := stream.read(1024 * 1024):
                        count += len(block)
                        require(
                            count <= info.st_size and time.monotonic() < deadline,
                            "backup_full_read_bound",
                        )
                        hashed.update(block)
                    require(count == info.st_size, "backup_full_read_short")
                    h = hashed.hexdigest()
                end = os.fstat(file)
                require(
                    (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    == (end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns),
                    "backup_file_changed",
                )
                rows[name] = {
                    "bytes": info.st_size,
                    "sha256": h,
                    "mtime_ns": info.st_mtime_ns,
                    "ctime_ns": info.st_ctime_ns,
                    "inode": info.st_ino,
                    "device": info.st_dev,
                }
            finally:
                os.close(file)
        require(sorted(os.listdir(fd)) == names, "backup_directory_changed")
        space = os.fstatvfs(fd)
        return rows, space.f_bavail * space.f_frsize
    finally:
        os.close(fd)


def _sidecar(name):
    require(NAME.fullmatch(name) and name.endswith(".dump"), "backup_name")
    fd = _directory(ROOT + "/backups")
    try:
        return _read(fd, name + ".json", 16384)
    finally:
        os.close(fd)


async def _mounts(identity):
    sha(identity)
    return parse(
        await _capture(
            DOCKER
            + ("inspect", "--type", "container", "--format", MOUNT_FORMAT, identity)
        )
    )


async def _frozen(spec, baseline, journal):
    from host_producer_control import verify_frozen

    await verify_frozen(journal, baseline)
    require_host_authority(spec)
    exact(baseline, ROLES)
    require(
        digest(baseline) == parse(spec._binding)["baseline_sha256"],
        "backup_baseline_binding",
    )
    rows = {}
    for role in ROLES:
        sha(baseline[role])
        row = (await collect("inspect", role))["facts"]
        require(
            row["id"] == baseline[role]
            and not row["paused"]
            and not row["restarting"]
            and not row["oom"],
            "backup_container_identity",
        )
        require(
            row["running"] is (role not in ("worker", "beat")), "backup_container_state"
        )
        if role in ("api", "web", "worker", "beat"):
            require(row["restart_policy"] == "no", "backup_restart_policy")
        rows[role] = row
    require(rows["db"]["image"] == PG_IMAGE, "backup_pg_image")
    for timer in ("backup", "renew"):
        row = await observe_timer(timer)
        require(
            row["timer"]["active"] is False and row["timer"]["enabled"] is False,
            "backup_timer_active",
        )
    sessions = await two_idle_windows(baseline["db"])
    await verify_frozen(journal, baseline)
    require_host_authority(spec)
    return {"containers": rows, "sessions": sessions}


async def _sql(identity, script, timeout=180, role="root"):
    sha(identity)
    require(role in ("root", "clothing_app"), "restore_sql_role")
    return await _capture(
        DOCKER
        + (
            "exec",
            "-i",
            "-u",
            "0",
            identity,
            "psql",
            "-X",
            "-q",
            "-A",
            "-t",
            "--set=ON_ERROR_STOP=1",
            "--host=/var/run/postgresql",
            "--username=" + role,
            "--dbname=" + DB,
        ),
        stdin=script,
        timeout=timeout,
    )


def _action(spec, journal, step):
    require_step(spec, journal, step)
    manual_freeze(spec, journal)
    from host_sequence import replay

    require(
        "FREEZE_VERIFY" in replay(spec, journal.read()).completed,
        "backup_freeze_required",
    )
    return journal.read()[-1]["payload"]["action_id"]


def _once(journal, name, row):
    # O_EXCL create is a local no-retry fence, even when the caller re-enters the same intent.
    journal.directory.create(name, canonical(row))


async def backup(spec, journal, baseline, *, schema_before_sha256):
    sha(schema_before_sha256)
    action_id = _action(spec, journal, "BACKUP")
    freeze_before = await _frozen(spec, baseline, journal)
    rows = freeze_before["containers"]
    _script_pins()
    mounts = await _mounts(baseline["api"])
    require(
        mounts["id"] == baseline["api"]
        and mounts["image"] == rows["api"]["image"]
        and [
            m
            for m in mounts["mounts"]
            if m["destination"] == "/opt/production"
            or m["destination"].startswith("/opt/production/")
        ]
        == [
            {
                "source": ROOT + "/build/control",
                "destination": "/opt/production",
                "rw": False,
                "type": "bind",
            }
        ]
        and [
            m
            for m in mounts["mounts"]
            if m["destination"] == "/run/backups"
            or m["destination"].startswith("/run/backups/")
        ]
        == [
            {
                "source": ROOT + "/backups",
                "destination": "/run/backups",
                "rw": False,
                "type": "bind",
            }
        ],
        "backup_api_control_mount",
    )
    before, free = _snapshot()
    # The 1024 pre-STOP_API contract counts only blocking business sessions
    # for this legacy plan gate. The actual total/idle counts are retained in
    # both complete window observations; they are never represented as zero.
    blocking = sum(
        c[k]
        for window in freeze_before["sessions"]["windows"]
        for c in window["roles"].values()
        for k in ("active", "idle_in_transaction", "unknown")
    )
    backup_adapter.plan(
        {
            "api_running": True,
            "backup_lock_owned": False,
            "timers_idle": True,
            "sessions": blocking,
            "stored_bytes": sum(v["bytes"] for v in before.values()),
            "free_bytes": free,
        }
    )
    comparison = await _database_snapshot(baseline["db"])
    require_host_authority(spec)
    manual_freeze(spec, journal)
    freeze_tick = time.monotonic_ns()
    _once(
        journal,
        "backup-invocation.json",
        {
            "action_id": action_id,
            "policy_sha256": spec.sha256,
            "freeze_tick": freeze_tick,
            "before": before,
            "comparison": comparison,
            "containers": rows,
            "api_mounts": mounts,
            "freeze_observation": freeze_before,
            "legacy_plan_business_blocking_count": blocking,
        },
    )
    require_host_authority(spec)
    manual_freeze(spec, journal)
    started_tick, started_wall = time.monotonic_ns(), time.time_ns()
    output = parse(await _capture(command("backup", "api"), timeout=2100))
    finished_wall = time.time_ns()
    exact(
        output, ("status", "bytes", "local_and_offhost", "retention_deletion_enabled")
    )
    require(
        output["status"] == "PRODUCTION_BACKUP_VERIFIED"
        and output["local_and_offhost"] is True
        and output["retention_deletion_enabled"] is False,
        "backup_script_result",
    )
    _script_pins()
    after, _ = _snapshot()
    require(
        all(after.get(n) == v for n, v in before.items()), "existing_backup_changed"
    )
    added = set(after) - set(before)
    dumps = [n for n in added if n.endswith(".dump")]
    require(
        len(dumps) == 1 and added == {dumps[0], dumps[0] + ".json"},
        "unique_backup_pair",
    )
    name = dumps[0]
    for n in added:
        require(
            started_wall <= after[n]["mtime_ns"] <= finished_wall, "backup_wall_window"
        )
    raw = _sidecar(name)
    sidecar = parse(raw)
    exact(
        sidecar,
        (
            "archive_list",
            "archive_full_read",
            "offhost_verified",
            "bytes",
            "sha256",
            "existing_objects_changed",
        ),
    )
    require(
        sidecar["offhost_verified"] is True
        and sidecar["bytes"] == output["bytes"]
        and hashlib.sha256(raw).hexdigest() == after[name + ".json"]["sha256"],
        "backup_sidecar",
    )
    # The pinned original uploader issues GetObject and consumes its entire Body,
    # checks ContentLength and hashes every byte. This is not a HEAD attestation.
    observed_tick = time.monotonic_ns()
    projected = {
        n: {
            "bytes": v["bytes"],
            "sha256": v["sha256"],
            "created_tick": observed_tick,
            "kind": "dump" if n.endswith(".dump") else "sidecar",
        }
        for n, v in after.items()
    }
    old = {n: projected[n] for n in before}
    receipt = {
        "action_id": action_id,
        "started_tick": started_tick,
        "finished_tick": time.monotonic_ns(),
        "exit_code": 0,
        "archive_list": sidecar["archive_list"],
        "archive_full_read": sidecar["archive_full_read"],
        "local_sha256": after[name]["sha256"],
        "local_bytes": after[name]["bytes"],
        "r2_get_sha256": sidecar["sha256"],
        "r2_get_bytes": sidecar["bytes"],
        "r2_get_complete": sidecar["offhost_verified"],
        "existing_objects_changed": sidecar["existing_objects_changed"],
        "sidecar_sha256": after[name + ".json"]["sha256"],
        "api_id": baseline["api"],
    }
    verified = backup_adapter.verify(
        old, projected, receipt, freeze_tick, action_id, baseline["api"]
    )
    freeze_after = await _frozen(spec, baseline, journal)
    after_rows = freeze_after["containers"]
    require(
        {k: v["image"] for k, v in rows.items()}
        == {k: v["image"] for k, v in after_rows.items()},
        "backup_image_changed",
    )
    require(
        await _database_snapshot(baseline["db"]) == comparison,
        "backup_database_changed",
    )
    require_host_authority(spec)
    manual_freeze(spec, journal)
    private = {
        "action_id": action_id,
        "policy_sha256": spec.sha256,
        "receipt": receipt,
        "before": before,
        "after": after,
        "sidecar": sidecar,
        "comparison": comparison,
        "started_wall_ns": started_wall,
        "finished_wall_ns": finished_wall,
        "created_tick_semantics": "observed_full_read_complete_monotonic_ns; actual_mtime_window_separately_checked",
        "original_script_sha256": BACKUP_SHA,
        "original_uploader_sha256": UPLOAD_SHA,
        "freeze_before": freeze_before,
        "freeze_after": freeze_after,
    }
    journal.directory.create("backup-private.json", canonical(private))
    result = {
        **verified,
        "mode": "host_observed",
        "receipt_sha256": digest(private),
        "schema_before_sha256": schema_before_sha256,
    }
    journal.directory.create("backup-observed.json", canonical(result))
    return result


# Catalog projections use object/role names, not instance-local OIDs. No password
# columns or row contents leave PostgreSQL. Every result is a bounded SHA256.
CATALOGS = {
    "schema": """SELECT n.nspname,c.relname,c.relkind,a.attnum,a.attname,
        format_type(a.atttypid,a.atttypmod) typ,a.attnotnull,a.attidentity,a.attgenerated,
        pg_get_expr(d.adbin,d.adrelid) def
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')""",
    "owners": """SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) owner
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        UNION ALL SELECT 'database',datname,'d',pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()
        UNION ALL SELECT 'schema',nspname,'n',pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'
        UNION ALL SELECT 'function',proname||'('||pg_get_function_identity_arguments(oid)||')','f',pg_get_userbyid(proowner)
        FROM pg_proc WHERE pronamespace='public'::regnamespace
        UNION ALL SELECT 'type',typname,'t',pg_get_userbyid(typowner) FROM pg_type WHERE typnamespace='public'::regnamespace""",
    "acl": """SELECT 'relation' kind,n.nspname ns,c.relname name,c.relacl::text acl
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        UNION ALL SELECT 'schema','public',nspname,nspacl::text FROM pg_namespace WHERE nspname='public'
        UNION ALL SELECT 'database','',datname,datacl::text FROM pg_database WHERE datname=current_database()
        UNION ALL SELECT 'default',COALESCE(n.nspname,''),pg_get_userbyid(d.defaclrole)||':'||d.defaclobjtype::text,d.defaclacl::text
        FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
        UNION ALL SELECT 'column','public',c.relname||'.'||a.attname,a.attacl::text
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='public'::regnamespace AND a.attnum>0 AND NOT a.attisdropped
        UNION ALL SELECT 'function','public',p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',p.proacl::text
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
        UNION ALL SELECT 'type','public',typname,typacl::text FROM pg_type WHERE typnamespace='public'::regnamespace""",
    "roles": """SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,
        rolreplication,rolbypassrls,rolconnlimit FROM pg_roles
        WHERE rolname IN ('root','clothing_app','clothing_bypass','clothing_archiver')""",
    "references": """SELECT 'constraint' kind,c.conrelid::regclass::text name,c.conname sub,
        pg_get_constraintdef(c.oid) definition FROM pg_constraint c
        WHERE c.connamespace='public'::regnamespace
        UNION ALL SELECT 'index',tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'
        UNION ALL SELECT 'policy',c.relname,p.polname,
        concat_ws('|',p.polcmd,p.polpermissive,pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid),
        (SELECT string_agg(CASE WHEN r=0 THEN 'public' ELSE pg_get_userbyid(r) END,',' ORDER BY CASE WHEN r=0 THEN 'public' ELSE pg_get_userbyid(r) END) FROM unnest(p.polroles) r))
        FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid WHERE c.relnamespace='public'::regnamespace
        UNION ALL SELECT 'rls',relname,'',relrowsecurity::text||':'||relforcerowsecurity::text
        FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p')
        UNION ALL SELECT 'membership',pg_get_userbyid(roleid),pg_get_userbyid(member),concat_ws('|',admin_option,inherit_option,set_option)
        FROM pg_auth_members WHERE pg_get_userbyid(roleid) LIKE 'clothing_%' OR pg_get_userbyid(member) LIKE 'clothing_%'
        UNION ALL SELECT 'function',n.nspname,p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind IN ('f','p')
        UNION ALL SELECT 'extension',extname,extversion,'' FROM pg_extension""",
}


# Only PG18's observed deparse move: varchar literal array -> per-literal text.
# These two complete CHECK forms retain every literal, its order, and operator.
CHECK_LITERAL = r"'(?:[^']|'')*'"
CHECK_LITERAL_CAST = "(" + CHECK_LITERAL + r")::character varying"
_CHECK_ITEMS = (
    "("
    + CHECK_LITERAL
    + r"::character varying(?:, "
    + CHECK_LITERAL
    + r"::character varying)*)"
)
_CHECK_COLUMN = r"([a-z_][a-z0-9_]*)"
CHECK_FORMS = (
    (
        r"^CHECK \(\(\("
        + _CHECK_COLUMN
        + r"\)::text = ANY \(\(ARRAY\["
        + _CHECK_ITEMS
        + r"\]\)::text\[\]\)\)\)$",
        "CHECK ((({column})::text = ANY ((ARRAY[{items}])::text[])))",
        "CHECK ((({column})::text = ANY (ARRAY[{items}])))",
    ),
    (
        r"^CHECK \(\(\("
        + _CHECK_COLUMN
        + r" IS NULL\) OR \(\(\1\)::text = ANY \(\(ARRAY\["
        + _CHECK_ITEMS
        + r"\]\)::text\[\]\)\)\)\)$",
        "CHECK ((({column} IS NULL) OR (({column})::text = ANY ((ARRAY[{items}])::text[]))))",
        "CHECK ((({column} IS NULL) OR (({column})::text = ANY (ARRAY[{items}]))))",
    ),
)


def canonical_check_definition(value):
    """Same finite grammar used by SQL; useful for inert evidence/negative checks."""
    for pattern, source, target in CHECK_FORMS:
        matched = re.fullmatch(pattern, value)
        if matched and source.format(column=matched[1], items=matched[2]) == value:
            items = re.sub(
                CHECK_LITERAL_CAST, r"(\1::character varying)::text", matched[2]
            )
            return target.format(column=matched[1], items=items)
    return value


def canonical_acl_query(query):
    # Sort complete aclitems, not their permissions; NULL, empty and duplicates
    # remain distinct. PostgreSQL retains grantee/grantor and grantable markers.
    return (
        "SELECT kind,ns,name,CASE WHEN acl IS NULL THEN NULL ELSE "
        'ARRAY(SELECT item FROM unnest(acl::aclitem[]) item ORDER BY item::text COLLATE "C")::text END acl '
        "FROM (" + query + ") original_acl"
    )


def _check_sql_template(template, column, items):
    return " || ".join(
        {"{column}": column, "{items}": items}.get(
            part, "'" + part.replace("'", "''") + "'"
        )
        for part in re.split(r"(\{column\}|\{items\})", template)
        if part
    )


def canonical_references_query(query):
    arms, matches = [], []
    for index, (pattern, source, target) in enumerate(CHECK_FORMS):
        field = "m.p" + str(index)
        matches.append(
            "regexp_match(definition,$flow78$" + pattern + "$flow78$) p" + str(index)
        )
        replacement = (
            r"regexp_replace("
            + field
            + r"[2],$flow78$"
            + CHECK_LITERAL_CAST
            + r"$flow78$,$flow78$(\1::character varying)::text$flow78$,'g')"
        )
        original = _check_sql_template(source, field + "[1]", field + "[2]")
        canonical = _check_sql_template(target, field + "[1]", replacement)
        # Reconstruct the entire source too: regex anchors may not discard even
        # an extra trailing newline or any syntax outside these exact forms.
        arms.append(
            "WHEN kind='constraint' AND "
            + field
            + " IS NOT NULL AND definition=("
            + original
            + ") THEN "
            + canonical
        )
    return (
        "SELECT kind,name,sub,CASE "
        + " ".join(arms)
        + " ELSE definition END definition "
        "FROM ("
        + query
        + ") original_refs CROSS JOIN LATERAL (SELECT "
        + ",".join(matches)
        + ") m"
    )


RAW_CATALOGS = dict(CATALOGS)
CATALOGS["acl"] = canonical_acl_query(RAW_CATALOGS["acl"])
CATALOGS["references"] = canonical_references_query(RAW_CATALOGS["references"])


def _hash_query(query):
    # SHA256 each canonical JSON row, retain duplicate multiplicities, then sort
    # and hash the complete multiset. statement_timeout and output cap are fixed.
    return (
        "SELECT encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') "
        "FROM (SELECT encode(sha256(convert_to(row_to_json(q)::text,'UTF8')),'hex') h FROM ("
        + query
        + ") q) hashes;"
    )


async def _query_hash(identity, query, role="root"):
    script = (
        "BEGIN READ ONLY; SET LOCAL statement_timeout='150s'; SET LOCAL lock_timeout='5s'; "
        "SET LOCAL timezone='UTC'; SET LOCAL extra_float_digits=3; SET LOCAL work_mem='16MB'; SET LOCAL temp_file_limit='256MB'; "
        + _hash_query(query)
        + " ROLLBACK;"
    ).encode()
    value = (
        (await _sql(identity, script, timeout=165, role=role)).decode("ascii").strip()
    )
    sha(value)
    return value


async def _rls(identity, tenants):
    # Use actual independent ordinary-role Unix-socket sessions, not SET ROLE.
    role = parse(
        await _sql(
            identity,
            b"SELECT json_build_object('usr',current_user,'super',rolsuper,'bypass',rolbypassrls) FROM pg_roles WHERE rolname=current_user;",
            role="clothing_app",
        )
    )
    require(
        role == {"usr": "clothing_app", "super": False, "bypass": False},
        "restore_app_role",
    )
    out = []
    for tenant in ["00000000-0000-0000-0000-000000000000", *tenants]:
        require(
            re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", tenant),
            "restore_tenant_id",
        )
        for table in ("promotion", "promotion_platform_setting"):
            expected = await _query_hash(
                identity,
                "SELECT * FROM public."
                + table
                + " WHERE tenant_id='"
                + tenant
                + "'::uuid",
            )
            script = (
                "BEGIN READ ONLY; SET LOCAL statement_timeout='150s'; SET LOCAL lock_timeout='5s'; "
                "SET LOCAL timezone='UTC'; SET LOCAL extra_float_digits=3; SET LOCAL app.bypass_rls='off'; "
                "SET LOCAL app.tenant_id='"
                + tenant
                + "'; "
                + _hash_query("SELECT * FROM public." + table)
                + " ROLLBACK;"
            ).encode()
            actual = (
                (await _sql(identity, script, timeout=165, role="clothing_app"))
                .decode("ascii")
                .strip()
            )
            sha(actual)
            require(actual == expected, "restore_rls_visibility")
            out.append(
                {
                    "tenant_sha256": hashlib.sha256(tenant.encode()).hexdigest(),
                    "table": table,
                    "sha256": actual,
                }
            )
    return digest(out)


async def _database_snapshot(identity):
    async with asyncio.timeout(600):
        return await _database_snapshot_body(identity)


async def _database_snapshot_body(identity):
    info = parse(
        await _sql(
            identity,
            b"""BEGIN READ ONLY; SET LOCAL statement_timeout='10s';
SELECT json_build_object('database',current_database(),'version',current_setting('server_version_num'),
'head',(SELECT version_num FROM public.alembic_version),
'tables',(SELECT json_agg(relname ORDER BY relname) FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p')),
'sequences',(SELECT COALESCE(json_agg(relname ORDER BY relname),'[]'::json) FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='S'),
'tenants',(SELECT json_agg(id::text ORDER BY id) FROM public.tenant),
'test_rows',(SELECT count(*)>0 FROM public.promotion) AND (SELECT count(*)>0 FROM public.promotion_platform_setting));
ROLLBACK;""",
        )
    )
    require(
        info["database"] == DB
        and info["version"] == "180006"
        and info["head"] == BEFORE,
        "restore_database_identity",
    )
    require(
        type(info["tables"]) is list
        and 1 <= len(info["tables"]) <= 512
        and type(info["sequences"]) is list
        and len(info["sequences"]) <= 512
        and type(info["tenants"]) is list
        and 1 <= len(info["tenants"]) <= 128
        and info["test_rows"] is True
        and "00000000-0000-0000-0000-000000000000" not in info["tenants"],
        "restore_snapshot_bounds",
    )
    result = {
        key + "_sha256": await _query_hash(identity, query)
        for key, query in CATALOGS.items()
    }
    rows = {}
    for table in info["tables"]:
        require(re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", table), "restore_table_name")
        count = await _sql(
            identity,
            (
                "BEGIN READ ONLY; SET LOCAL statement_timeout='30s'; SELECT count(*) FROM public.\""
                + table
                + '"; ROLLBACK;'
            ).encode(),
            timeout=40,
        )
        require(
            re.fullmatch(rb"[0-9]+\n", count) and int(count) <= 2000000,
            "restore_table_hash_bound",
        )
        rows["table:" + table] = await _query_hash(
            identity, 'SELECT * FROM public."' + table + '"'
        )
    for sequence in info["sequences"]:
        require(
            re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", sequence), "restore_sequence_name"
        )
        rows["sequence:" + sequence] = await _query_hash(
            identity, 'SELECT last_value,is_called FROM public."' + sequence + '"'
        )
    result["rows_sha256"] = digest(rows)
    result["rls_sha256"] = await _rls(identity, info["tenants"])
    result["pg_version"], result["head"] = "18.6", BEFORE
    return result


def _new_drill_directory(spec):
    root = ROOT + "/releases/" + spec.data["release_id"]
    fd = _directory(root)
    try:
        os.mkdir("drill", 0o700, dir_fd=fd)
        os.fsync(fd)
        drill = os.open(
            "drill", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
        )
        try:
            os.mkdir("data", 0o700, dir_fd=drill)
            os.fsync(drill)
            data = os.stat("data", dir_fd=drill, follow_symlinks=False)
            require(
                data.st_uid == data.st_gid == 0 and stat.S_IMODE(data.st_mode) == 0o700,
                "restore_new_data_owner",
            )
            return {
                "path": root + "/drill/data",
                "inode": data.st_ino,
                "device": data.st_dev,
            }
        finally:
            os.close(drill)
    finally:
        os.close(fd)


async def _postgres_uid(identity):
    sha(identity)
    raw = (
        await _capture(DOCKER + ("exec", identity, "id", "-u", "postgres"), timeout=10)
    ).strip()
    require(raw.isdigit() and 0 < int(raw) < 2**31, "restore_postgres_uid")
    return int(raw)


def _data_identity(data, *, owner=(0, 0)):
    # PG entrypoint chown postgres changes only UID of this new bind root. Its
    # initial root GID, private mode and inode stay fixed; outer dirs stay root.
    path = Path(data["path"])
    parent = _directory(path.parent)
    try:
        outer = os.fstat(parent)
        require(
            outer.st_uid == outer.st_gid == 0 and stat.S_IMODE(outer.st_mode) == 0o700,
            "restore_outer_directory_owner",
        )
        fd = os.open(
            path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        try:
            info = os.fstat(fd)
            require(
                info.st_ino == data["inode"]
                and info.st_dev == data["device"]
                and (info.st_uid, info.st_gid) == owner
                and stat.S_IMODE(info.st_mode) == 0o700,
                "restore_data_identity",
            )
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _restore_privileges(observed):
    # Docker's observed canonical CapAdd representation; reject extras/duplicates.
    added = observed.get("cap_add")
    return (
        observed.get("privileged") is False
        and type(added) is list
        and all(type(value) is str for value in added)
        and sorted(added) == ["CAP_CHOWN", "CAP_SETGID", "CAP_SETUID"]
        and observed.get("cap_drop") == ["ALL"]
    )


def _check_drill_projection(observed, identity, image, data_path, dump_path):
    expected_mounts = [
        {
            "source": data_path,
            "destination": "/var/lib/postgresql",
            "rw": True,
            "type": "bind",
        },
        {
            "source": dump_path,
            "destination": "/input/database.dump",
            "rw": False,
            "type": "bind",
        },
    ]
    require(
        observed["id"] == identity
        and observed["image"] == image
        and _restore_privileges(observed)
        and observed["network"] == "none"
        and observed["ports"] in (None, {})
        and observed.get("tmpfs")
        == {"/var/run/postgresql": restore_drill.SOCKET_TMPFS.split(":", 1)[1]}
        and sorted(observed["mounts"], key=lambda x: x["destination"])
        == sorted(expected_mounts, key=lambda x: x["destination"]),
        "restore_container_isolation",
    )


async def _drill_identity(identity, data, dump_name, *, owner=(0, 0)):
    observed = await _mounts(identity)
    # Legacy --tmpfs appears only in HostConfig.Tmpfs, not in top-level Mounts.
    _check_drill_projection(
        observed, identity, PG_IMAGE, data["path"], ROOT + "/backups/" + dump_name
    )
    _data_identity(data, owner=owner)
    observed["data_owner"] = {"uid": owner[0], "gid": owner[1], "mode": 0o700}
    return observed


FINAL_POSTMASTER_PROBE = """set -u
# During image initialization the accepting temporary postmaster is a child of
# entrypoint. Only the final exec'd PID1 may authorize the first restore SQL.
if [ "$(cat /proc/1/comm)" != postgres ]; then printf initializing; exit 0; fi
if ! test /proc/1/exe -ef "$(command -v postgres)"; then printf unknown_exe; exit 0; fi
if ! read -r data_pid < /var/lib/postgresql/postmaster.pid; then printf initializing; exit 0; fi
if ! read -r socket_pid < /var/run/postgresql/.s.PGSQL.5432.lock; then printf initializing; exit 0; fi
if [ "$data_pid" != 1 ] || [ "$socket_pid" != 1 ]; then printf initializing; exit 0; fi
if ! test -S /var/run/postgresql/.s.PGSQL.5432; then printf initializing; exit 0; fi
before=$(awk '{print $22}' /proc/1/stat)
pg_isready -q -h /var/run/postgresql -U root -d clothing_erp_prod_20261018
status=$?
if [ "$status" != 0 ]; then printf 'starting:%s' "$status"; exit 0; fi
after=$(awk '{print $22}' /proc/1/stat)
read -r data_pid < /var/lib/postgresql/postmaster.pid || exit 1
read -r socket_pid < /var/run/postgresql/.s.PGSQL.5432.lock || exit 1
if [ "$before" != "$after" ] || [ "$data_pid" != 1 ] || [ "$socket_pid" != 1 ]; then printf unknown_changed; exit 0; fi
if ! test /proc/1/exe -ef "$(command -v postgres)"; then printf unknown_exe; exit 0; fi
printf 'ready:%s' "$after"
"""


async def _ready(identity):
    sha(identity)
    for _ in range(30):
        # Same UID permits /proc executable identity reads without adding caps.
        # These bounded startup polls never repeat any SQL or container write.
        raw = await _capture(
            DOCKER
            + (
                "exec",
                "--user",
                "postgres",
                identity,
                "sh",
                "-c",
                FINAL_POSTMASTER_PROBE,
            )
        )
        if re.fullmatch(rb"ready:[1-9][0-9]*", raw):
            return {
                "pid": 1,
                "start_ticks": int(raw.split(b":")[1]),
                "final_postmaster_executable_verified": True,
                "socket_owner_pid": 1,
            }
        require(
            raw in (b"initializing", b"starting:1", b"starting:2"), "restore_readiness"
        )
        await asyncio.sleep(1)
    require(False, "restore_readiness_timeout")


async def restore(spec, journal, baseline):
    action_id = _action(spec, journal, "RESTORE")
    freeze_before = await _frozen(spec, baseline, journal)
    backup_raw = journal.directory.read("backup-observed.json", 16384)
    backup_result = parse(backup_raw)
    private_raw = journal.directory.read("backup-private.json", 8 * 1024**2)
    evidence = parse(private_raw)
    require(
        backup_result["mode"] == "host_observed"
        and digest(evidence) == backup_result["receipt_sha256"]
        and evidence["policy_sha256"] == spec.sha256,
        "restore_backup_receipt",
    )
    history = journal.read()
    prior = [
        i
        for i, row in enumerate(history)
        if row["kind"] == "intent" and row["payload"]["name"] == "BACKUP"
    ]
    require(len(prior) == 1 and prior[0] + 1 < len(history), "restore_backup_history")
    intent, result = history[prior[0]], history[prior[0] + 1]
    require(
        result["kind"] == "result"
        and result["payload"]["completed"] is True
        and result["payload"]["facts_sha256"] == digest(backup_result)
        and result["payload"]["action_id"]
        == intent["payload"]["action_id"]
        == evidence["action_id"],
        "restore_backup_journal_binding",
    )
    name = backup_result["dump_name"]
    planned = restore_drill.plan(spec.data["source_commit"], name, PG_IMAGE)
    current, _ = _snapshot()
    require(
        current == evidence["after"]
        and current[name]["sha256"] == backup_result["dump_sha256"],
        "restore_backup_changed",
    )
    expected = evidence["comparison"]
    require(
        await _database_snapshot(baseline["db"]) == expected, "restore_source_changed"
    )
    postgres_uid = await _postgres_uid(baseline["db"])
    require_host_authority(spec)
    manual_freeze(spec, journal)
    _once(
        journal,
        "restore-invocation.json",
        {
            "action_id": action_id,
            "policy_sha256": spec.sha256,
            "backup_receipt_sha256": backup_result["receipt_sha256"],
        },
    )
    data = _new_drill_directory(spec)
    require(data["path"] == planned["data_path"], "restore_exclusive_path")
    # Original plan supplies isolation/bind mounts; only explicit reviewed
    # initialization parameters/capabilities are added, never a production secret.
    argv = (
        DOCKER
        + planned["create_argv"][1:-1]
        + (
            "--cap-add=CHOWN",
            "--cap-add=SETUID",
            "--cap-add=SETGID",
            "-e",
            "POSTGRES_USER=root",
            "-e",
            "POSTGRES_DB=" + DB,
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            PG_IMAGE,
            "postgres",
            "-c",
            "listen_addresses=",
        )
    )
    require_host_authority(spec)
    identity = (await _capture(argv, timeout=30)).decode("ascii").strip()
    sha(identity)
    before = await _drill_identity(identity, data, name)
    journal.directory.create(
        "restore-created.json",
        canonical({"container_id": identity, "data": data, "inspection": before}),
    )
    require_host_authority(spec)
    await _capture(DOCKER + ("start", identity), timeout=30)
    await _ready(identity)
    await _drill_identity(identity, data, name, owner=(postgres_uid, 0))
    require_host_authority(spec)
    manual_freeze(spec, journal)
    await _sql(identity, ROLE_SQL)
    require_host_authority(spec)
    manual_freeze(spec, journal)
    await _capture(
        DOCKER
        + (
            "exec",
            "-u",
            "0",
            identity,
            "pg_restore",
            "--exit-on-error",
            "--host=/var/run/postgresql",
            "--username=root",
            "--dbname=" + DB,
            "/input/database.dump",
        ),
        timeout=1800,
    )
    comparison = await _database_snapshot(identity)
    require(comparison["rls_sha256"] == expected["rls_sha256"], "restore_rls_baseline")
    after = await _drill_identity(identity, data, name, owner=(postgres_uid, 0))
    freeze_after = await _frozen(spec, baseline, journal)
    require(
        await _database_snapshot(baseline["db"]) == expected, "restore_source_changed"
    )
    observed = {
        "container": identity,
        "network_mode": "none",
        "ports": [],
        "data_path": data["path"],
        "production_mounts": [],
        "dump_sha256": backup_result["dump_sha256"],
        "pg_version": comparison["pg_version"],
        "head": comparison["head"],
        "exit_code": 0,
        "app_superuser": False,
        "app_bypassrls": False,
        "rls_app_checked": True,
        **{
            k: comparison[k]
            for k in (
                "schema_sha256",
                "rows_sha256",
                "acl_sha256",
                "owners_sha256",
                "roles_sha256",
                "references_sha256",
            )
        },
        "original_db_id": baseline["db"],
        "original_redis_id": baseline["redis"],
        "writes_still_paused": True,
    }
    accepted = restore_drill.verify(
        observed,
        {
            **observed,
            **{
                k: expected[k]
                for k in comparison
                if k.endswith("_sha256") and k != "rls_sha256"
            },
        },
    )
    private = {
        "action_id": action_id,
        "policy_sha256": spec.sha256,
        "backup_receipt_sha256": backup_result["receipt_sha256"],
        "observed": observed,
        "expected": expected,
        "rls_sha256": comparison["rls_sha256"],
        "inspection": after,
        "role_bootstrap_sha256": hashlib.sha256(ROLE_SQL).hexdigest(),
        "freeze_before": freeze_before,
        "freeze_after": freeze_after,
    }
    require_host_authority(spec)
    manual_freeze(spec, journal)
    journal.directory.create("restore-private.json", canonical(private))
    result = {**accepted, "mode": "host_observed", "receipt_sha256": digest(private)}
    journal.directory.create("restore-observed.json", canonical(result))
    return result
