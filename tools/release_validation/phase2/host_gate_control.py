"""Fixed nginx worker drain, ACME readback and independent board preservation."""

import asyncio
import http.client
import os
import ssl
import time

from contracts import canonical, exact, require
from dashboard_contract import BOARD_ID, BOARD_IMAGE, verify_members
from fixed_host_transport import DOCKER, ROOT, _capture, collect, command
from host_backup_control import _directory
from host_release_input import require_step
from sql_policy import parse, sha

BOARD_PROBE = b"""import datetime,json,os,stat,sys,time
sys.path.insert(0,'/app')
from snapshot_payload import validate_snapshot
fd=os.open('/data/latest-snapshot.json',os.O_RDONLY|os.O_NOFOLLOW)
try:
 s=os.fstat(fd)
 if not (stat.S_ISREG(s.st_mode) and s.st_uid==10001 and s.st_nlink==1 and 0<s.st_size<=3*1024*1024+4096):raise ValueError('metadata')
 raw=os.read(fd,3*1024*1024+4097);end=os.fstat(fd)
 if len(raw)!=s.st_size or (s.st_ino,s.st_size,s.st_mtime_ns)!=(end.st_ino,end.st_size,end.st_mtime_ns):raise ValueError('changed')
finally:os.close(fd)
def bad(value):raise ValueError('constant')
data=json.loads(raw,parse_constant=bad)
snapshot=validate_snapshot(data['snapshot'])
received=data['receivedAt'];generated=snapshot['meta']['generatedAt']
def stamp(value):
 dt=datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
 if dt.tzinfo is None:raise ValueError('timezone')
 return dt.timestamp()
now=time.time();r=stamp(received);g=stamp(generated)
if max(r,g)>now+60:raise ValueError('future')
print(json.dumps({'received_at':received,'generated_at':generated,'received_epoch':r,'generated_epoch':g,'received_age_seconds':max(0,int(now-r)),'generated_age_seconds':max(0,int(now-g)),'schema_valid':True},sort_keys=True))
"""


async def dashboard_state(web_id):
    board = (await collect("dashboard_board", "web"))["facts"]
    network = (await collect("dashboard_network", "web"))["facts"]
    verify_members(network, board, web_id)
    require(board["image"] == BOARD_IMAGE, "gate_board_image")
    row = parse(
        await _capture(
            DOCKER + ("exec", "--user", "10001:10001", "-i", BOARD_ID, "python", "-I", "-B", "-"),
            stdin=BOARD_PROBE,
            timeout=15,
        )
    )
    exact(
        row,
        (
            "received_at",
            "generated_at",
            "received_epoch",
            "generated_epoch",
            "received_age_seconds",
            "generated_age_seconds",
            "schema_valid",
        ),
    )
    require(
        row["schema_valid"] is True
        and all(
            type(row[k]) is int and row[k] >= 0
            for k in ("received_age_seconds", "generated_age_seconds")
        ),
        "gate_board_snapshot",
    )
    from public_gate_observer import observe_dashboard

    return {
        "snapshot": row,
        "routes": await asyncio.to_thread(observe_dashboard),
        "network": network,
        "board": board,
    }


def _proc(pid):
    require(type(pid) is int and 0 < pid < 2**31, "nginx_pid")
    try:
        fd = os.open("/proc/" + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        values = {}
        for name in ("stat", "cmdline"):
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                raw = os.read(child, 8193)
                require(len(raw) <= 8192, "nginx_proc_size")
                values[name] = raw
            finally:
                os.close(child)
        parts = values["stat"].decode("ascii").rsplit(")", 1)[1].split()
        return {
            "pid": pid,
            "state": parts[0],
            "ppid": int(parts[1]),
            "start_ticks": int(parts[19]),
            "command": values["cmdline"].rstrip(b"\0"),
        }
    except FileNotFoundError:
        return None
    finally:
        os.close(fd)


def _nginx_master(master_pid):
    master = _proc(master_pid)
    require(
        master is not None
        and master["state"] not in ("Z", "X")
        and master["command"].startswith(b"nginx: master process nginx ")
        and b"-c /opt/production/nginx-active.conf" in master["command"],
        "nginx_master_identity",
    )
    return master


def nginx_workers(master_pid):
    master = _nginx_master(master_pid)
    path = "/proc/" + str(master_pid) + "/task/" + str(master_pid) + "/children"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = os.read(fd, 65537)
    finally:
        os.close(fd)
    require(len(raw) <= 65536, "nginx_children_bound")
    workers = []
    for token in raw.split():
        require(token.isdigit(), "nginx_child_pid")
        child = _proc(int(token))
        require(
            child is not None
            and child["ppid"] == master_pid
            and child["command"].startswith(b"nginx: worker process"),
            "nginx_child_identity",
        )
        workers.append({k: child[k] for k in ("pid", "start_ticks")})
    require(0 < len(workers) <= 256, "nginx_workers_count")
    return {
        "master": {k: master[k] for k in ("pid", "start_ticks")},
        "workers": workers,
    }


async def wait_old_workers(original):
    deadline = time.monotonic() + 120
    while True:
        master = _nginx_master(original["master"]["pid"])
        require(
            {k: master[k] for k in ("pid", "start_ticks")} == original["master"],
            "nginx_master_changed",
        )
        remaining = []
        for old in original["workers"]:
            now = _proc(old["pid"])
            if now is not None and now["start_ticks"] == old["start_ticks"]:
                remaining.append(old)
        if not remaining:
            # Exiting old children can disappear or have an empty cmdline while
            # being reaped. Inspect the new live set only after they are gone.
            current = nginx_workers(original["master"]["pid"])
            require(current["master"] == original["master"], "nginx_master_changed")
            return current
        require(time.monotonic() < deadline, "nginx_old_workers_not_drained")
        await asyncio.sleep(0.25)


def acme_file(spec):
    name = "flow78-proof-" + spec.data["source_commit"][:12]
    payload = (sha((spec.sha256 + ":acme").encode()) + "\n").encode()
    directory = _directory(ROOT + "/acme/.well-known/acme-challenge")
    try:
        file = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=directory,
        )
        with os.fdopen(file, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)
    return "/.well-known/acme-challenge/" + name, payload


def read_acme(path, payload):
    result = {}
    for port in (80, 443):
        connection = (
            http.client.HTTPSConnection(
                "47.110.76.9", 443, timeout=5, context=ssl.create_default_context()
            )
            if port == 443
            else http.client.HTTPConnection("47.110.76.9", 80, timeout=5)
        )
        try:
            connection.request(
                "GET", path, headers={"Host": "47.110.76.9", "Connection": "close"}
            )
            response = connection.getresponse()
            require(
                response.status == 200 and response.read(1025) == payload,
                "acme_challenge_readback",
            )
            result[str(port)] = 200
        finally:
            connection.close()
    return result


async def close_gate(spec, journal, baseline):
    require_step(spec, journal, "CLOSE_GATE")
    web = (await collect("inspect", "web"))["facts"]
    require(web["id"] == baseline["web"], "gate_web_identity")
    original = nginx_workers(web["pid"])
    board_before = await dashboard_state(web["id"])
    journal.directory.create(
        "nginx-before-close.json",
        canonical({"web": web, "processes": original, "dashboard": board_before}),
    )
    from host_web_control import switch_active

    switched = switch_active(spec, journal, "CLOSE_GATE")
    for operation in ("nginx_test", "nginx_reload"):
        argv = tuple(
            web["id"] if value == "clothinkai-prod-web-20261018" else value
            for value in command(operation, "web")
        )
        await _capture(argv, timeout=15)
    current = await wait_old_workers(original)
    from maintenance import PROBES
    from public_gate_observer import observe_gate

    public = await asyncio.to_thread(observe_gate)
    require(
        public["public_status"] == {path: 503 for path in PROBES},
        "gate_erp_maintenance",
    )
    challenge = acme_file(spec)
    acme = await asyncio.to_thread(read_acme, *challenge)
    board_after = await dashboard_state(web["id"])
    require(
        all(
            board_after["snapshot"][key] >= board_before["snapshot"][key]
            for key in ("received_epoch", "generated_epoch")
        ),
        "dashboard_snapshot_regressed",
    )
    after = (await collect("inspect", "web"))["facts"]
    require(
        after["id"] == web["id"]
        and after["image"] == web["image"]
        and after["pid"] == web["pid"],
        "gate_web_changed",
    )
    facts = {
        "mode": "host_observed",
        "syntax_ok": True,
        "reload_ok": True,
        "old_nginx_workers": 0,
        "old_connections": 0,
        "public_status": public["public_status"],
        "tls_verified": public["tls_verified"],
        "acme_status": acme,
        "dashboard_before": board_before,
        "dashboard_after": board_after,
        "processes_after": current,
        "config": switched,
        "closed_monotonic_ns": time.monotonic_ns(),
        "gate_closed": True,
        "observation_vantage": "target_host_public_origin",
    }
    journal.directory.create("gate-closed-observed.json", canonical(facts))
    return facts
