"""Disposable nginx: exact release templates, local TLS, routes and real worker drain."""

import asyncio
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_contract import validate_routes
from fixed_host_transport import _capture
from host_gate_control import nginx_workers, wait_old_workers
from host_web_control import TEMPLATE_PINS, _atomic_replace
from maintenance import PROBES

PUBLIC_HOST = "47.110.76.9"
STUB = r"""import json,sys,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
role=sys.argv[2]
class Handler(BaseHTTPRequestHandler):
 counter=0
 lock=threading.Lock()
 def log_message(self,*args): pass
 def do_GET(self):
  if role=='api' and self.path=='/api/hold':
   Path('/events/entered').write_text('entered')
   deadline=time.monotonic()+20
   while not Path('/events/release').exists():
    if time.monotonic()>deadline: raise TimeoutError('synthetic upstream hold')
    time.sleep(.05)
  with self.lock:
   Handler.counter+=1
   count=Handler.counter
  raw=json.dumps({'role':role,'path':self.path,'host':self.headers.get('Host'),
                  'forwarded_proto':self.headers.get('X-Forwarded-Proto'),'counter':count}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
ThreadingHTTPServer(('0.0.0.0',int(sys.argv[1])),Handler).serve_forever()
"""


def call(*args):
    try:
        return subprocess.check_output(
            ("docker", *args), stderr=subprocess.PIPE, timeout=30
        ).strip()
    except subprocess.CalledProcessError as error:
        print(
            json.dumps(
                {
                    "synthetic_docker_error": error.stderr[-8192:].decode(
                        errors="replace"
                    )
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        raise


class LocalTLS(http.client.HTTPSConnection):
    def connect(self):
        # The socket can reach only the inspected disposable container IP.
        self.sock = self._context.wrap_socket(
            socket.create_connection((self.host, self.port), self.timeout),
            server_hostname=PUBLIC_HOST,
        )


def request(address, context, path, secure=True):
    connection = (
        LocalTLS(address, 443, timeout=10, context=context)
        if secure
        else http.client.HTTPConnection(address, 80, timeout=10)
    )
    try:
        connection.request(
            "GET", path, headers={"Host": PUBLIC_HOST, "Connection": "close"}
        )
        response = connection.getresponse()
        body = response.read(65537)
        assert len(body) <= 65536
        return response.status, dict(response.getheaders()), body
    finally:
        connection.close()


async def ready(address, context):
    deadline = time.monotonic() + 10
    while True:
        try:
            result = await asyncio.to_thread(request, address, context, "/ready")
            if result[0] == 200:
                assert json.loads(result[2])["role"] == "api"
                return
            assert result[0] in (502, 503), "unexpected fixture startup response"
        except (OSError, http.client.HTTPException):
            pass
        assert time.monotonic() < deadline, "nginx fixture startup deadline"
        await asyncio.sleep(0.1)


async def routes(address, context, challenge, payload, *, closed):
    results = {}
    for secure in (False, True):
        for path in PROBES if closed else ("/", "/api/fixture", "/health", "/ready"):
            status, headers, body = await asyncio.to_thread(
                request, address, context, path, secure
            )
            if closed:
                assert status == 503 and headers.get("Retry-After") == "300"
            elif not secure:
                assert (
                    status == 308
                    and headers["Location"] == "https://" + PUBLIC_HOST + path
                )
            elif path == "/":
                assert status == 200 and body == b"synthetic frontend\n"
            else:
                assert status == 200 and json.loads(body)["role"] == "api"
            results[("https:" if secure else "http:") + path] = status
        status, _, body = await asyncio.to_thread(
            request, address, context, challenge, secure
        )
        assert status == 200 and body == payload
        results[("https:" if secure else "http:") + "acme"] = status
    for path in ("/progress", "/progress/health?fixture=1"):
        status, _, body = await asyncio.to_thread(request, address, context, path)
        row = json.loads(body)
        assert status == 200 and row["role"] == "dashboard" and row["path"] == path
        assert row["host"] == PUBLIC_HOST and row["forwarded_proto"] == "https"
        results[path] = {"status": status, "counter": row["counter"]}
    status, _, body = await asyncio.to_thread(
        request, address, context, "/progress-not-a-route"
    )
    assert status == (503 if closed else 200)
    assert closed or body == b"synthetic frontend\n"
    return results


async def exercise(root, python_image, nginx_image):
    files = Path(__file__).resolve().parents[1] / "templates"
    opened = (files / "nginx-release-open.conf").read_bytes()
    closed = (files / "nginx-release-closed.conf").read_bytes()
    for name, raw in (
        ("nginx-release-open.conf", opened),
        ("nginx-release-closed.conf", closed),
    ):
        assert hashlib.sha256(raw).hexdigest() == TEMPLATE_PINS[name]
        validate_routes(raw)
    for name in ("control", "stub", "events", "cert", "html", "acme"):
        (root / name).mkdir(mode=0o755 if name in ("html", "acme") else 0o700)
    challenge_dir = root / "acme/.well-known/acme-challenge"
    challenge_dir.mkdir(parents=True, mode=0o755)
    payload = b"synthetic-acme-route-proof\n"
    (challenge_dir / "ci-proof").write_bytes(payload)
    (root / "html/index.html").write_bytes(b"synthetic frontend\n")
    (root / "stub/server.py").write_text(STUB)
    await asyncio.to_thread(
        subprocess.run,
        (
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=" + PUBLIC_HOST,
            "-addext",
            "subjectAltName=IP:" + PUBLIC_HOST,
            "-keyout",
            str(root / "cert/privkey.pem"),
            "-out",
            str(root / "cert/fullchain.pem"),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
        timeout=20,
    )
    (root / "cert/privkey.pem").chmod(0o600)
    context = ssl.create_default_context(cafile=str(root / "cert/fullchain.pem"))
    control = os.open(root / "control", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    network, containers, held, draining = None, [], None, None
    try:
        _atomic_replace(control, None, opened, absent=True)
        network = call(
            "network",
            "create",
            "--internal",
            "--driver",
            "bridge",
            "flow78-nginx-ci-" + secrets.token_hex(8),
        ).decode()
        assert re.fullmatch(r"[0-9a-f]{64}", network)
        observed_network = json.loads(
            call("network", "inspect", "--format", "{{json .}}", network)
        )
        assert (
            observed_network["Id"] == network and observed_network["Internal"] is True
        )
        for role, port, alias in (
            ("api", 8000, "prod-api"),
            ("dashboard", 8080, "progress-dashboard"),
        ):
            identity = call(
                "run",
                "-d",
                "--pull=never",
                "--restart=no",
                "--network",
                network,
                "--network-alias",
                alias,
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges=true",
                "--memory=128m",
                "--pids-limit=32",
                "--mount",
                "type=bind,src=" + str(root / "stub") + ",dst=/fixture,readonly",
                "--mount",
                "type=bind,src=" + str(root / "events") + ",dst=/events",
                "--entrypoint",
                "python",
                python_image,
                "-I",
                "-B",
                "/fixture/server.py",
                str(port),
                role,
            ).decode()
            assert re.fullmatch(r"[0-9a-f]{64}", identity)
            containers.append(identity)
        web = call(
            "run",
            "-d",
            "--pull=never",
            "--restart=no",
            "--network",
            network,
            "--read-only",
            "--cap-drop=ALL",
            "--cap-add=CHOWN",
            "--cap-add=SETUID",
            "--cap-add=SETGID",
            "--cap-add=NET_BIND_SERVICE",
            "--security-opt=no-new-privileges=true",
            "--memory=256m",
            "--pids-limit=64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=32m",
            "--mount",
            "type=bind,src=" + str(root / "control") + ",dst=/opt/production,readonly",
            "--mount",
            "type=bind,src="
            + str(root / "cert")
            + ",dst=/etc/letsencrypt/live/clothinkai-ip,readonly",
            "--mount",
            "type=bind,src="
            + str(root / "html")
            + ",dst=/usr/share/nginx/html,readonly",
            "--mount",
            "type=bind,src=" + str(root / "acme") + ",dst=/var/www/acme,readonly",
            "--entrypoint",
            "nginx",
            nginx_image,
            "-c",
            "/opt/production/nginx-active.conf",
            "-g",
            "daemon off;",
        ).decode()
        assert re.fullmatch(r"[0-9a-f]{64}", web)
        containers.append(web)
        projection = '{"id":{{json .Id}},"image":{{json .Image}},"pid":{{json .State.Pid}},"ports":{{json .HostConfig.PortBindings}},"privileged":{{json .HostConfig.Privileged}},"cap_add":{{json .HostConfig.CapAdd}},"cap_drop":{{json .HostConfig.CapDrop}},"tmpfs":{{json .HostConfig.Tmpfs}},"networks":{{json .NetworkSettings.Networks}}}'
        observed = json.loads(call("inspect", "--format", projection, web))
        assert (
            observed["id"] == web
            and observed["image"] == nginx_image
            and observed["ports"] in (None, {})
            and observed["privileged"] is False
        )
        networks = list(observed["networks"].values())
        assert len(networks) == 1 and networks[0]["NetworkID"] == network
        address = networks[0]["IPAddress"]
        assert ipaddress.ip_address(address).is_private
        await ready(address, context)
        before = await routes(
            address,
            context,
            "/.well-known/acme-challenge/ci-proof",
            payload,
            closed=False,
        )
        original = nginx_workers(observed["pid"])
        held = asyncio.create_task(
            asyncio.to_thread(request, address, context, "/api/hold")
        )
        deadline = time.monotonic() + 5
        while not (root / "events/entered").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        _atomic_replace(control, opened, closed)
        call("exec", web, "nginx", "-t", "-c", "/opt/production/nginx-active.conf")
        call(
            "exec",
            web,
            "nginx",
            "-s",
            "reload",
            "-c",
            "/opt/production/nginx-active.conf",
        )
        draining = asyncio.create_task(wait_old_workers(original))
        await asyncio.sleep(0.5)
        assert not draining.done(), (
            "drain incorrectly completed while original request remained active"
        )
        maintenance = await routes(
            address,
            context,
            "/.well-known/acme-challenge/ci-proof",
            payload,
            closed=True,
        )
        assert not draining.done()
        (root / "events/release").write_bytes(b"release")
        assert (await held)[0] == 200
        closed_workers = await asyncio.wait_for(draining, 15)
        assert not {r["pid"] for r in original["workers"]} & {
            r["pid"] for r in closed_workers["workers"]
        }
        _atomic_replace(control, closed, opened)
        call("exec", web, "nginx", "-t", "-c", "/opt/production/nginx-active.conf")
        call(
            "exec",
            web,
            "nginx",
            "-s",
            "reload",
            "-c",
            "/opt/production/nginx-active.conf",
        )
        new_workers = await asyncio.wait_for(wait_old_workers(closed_workers), 15)
        after = await routes(
            address,
            context,
            "/.well-known/acme-challenge/ci-proof",
            payload,
            closed=False,
        )
        assert new_workers["master"] == original["master"]
        assert (
            call("inspect", "--format", "{{.Id}}", containers[1]).decode()
            == containers[1]
        )
        return {
            "mode": "real_linux_nginx_synthetic_upstreams",
            "nginx_image": nginx_image,
            "dependency_image": python_image,
            "template_sha256": {
                "open": hashlib.sha256(opened).hexdigest(),
                "closed": hashlib.sha256(closed).hexdigest(),
            },
            "network_id": network,
            "web_id": web,
            "web_isolation": observed,
            "before": before,
            "maintenance": maintenance,
            "after": after,
            "original_workers": original,
            "closed_workers": closed_workers,
            "reopened_workers": new_workers,
            "actual_pending_request_prevented_early_drain": True,
            "original_workers_exited": True,
            "tls_trust": "generated_local_fixture_certificate_only",
            "public_network_used": False,
            "production_dashboard_snapshot_checked": False,
            "host_release_spec_journal_executed": False,
            "production_29_actions_verified": False,
            "release_gate": "HOLD_remaining_release_validation",
        }
    except BaseException:
        for identity in containers:
            try:
                raw = await _capture(
                    (
                        "/usr/bin/timeout",
                        "10",
                        "/bin/sh",
                        "-c",
                        'docker logs --tail 60 "$1" 2>&1 | head -c 65536',
                        "nginx-fixture-diagnostic",
                        identity,
                    ),
                    timeout=15,
                )
                print(
                    json.dumps(
                        {
                            "synthetic_container_id": identity,
                            "bounded_log": raw.decode(errors="replace"),
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            except BaseException as diagnostic_error:  # noqa: BLE001 - retain original fixture error
                print(
                    json.dumps(
                        {
                            "synthetic_container_id": identity,
                            "diagnostic_error": type(diagnostic_error).__name__,
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
        raise
    finally:
        (root / "events/release").write_bytes(b"release")
        if draining is not None and not draining.done():
            draining.cancel()
            await asyncio.gather(draining, return_exceptions=True)
        if held is not None:
            await asyncio.gather(held, return_exceptions=True)
        os.close(control)
        for identity in reversed(containers):
            call("rm", "-f", identity)
        if network is not None and re.fullmatch(r"[0-9a-f]{64}", network):
            call("network", "rm", network)


def main():
    assert (
        sys.platform == "linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    images = [
        os.environ[k] for k in ("FLOW78_CI_PRODUCER_IMAGE", "FLOW78_CI_NGINX_IMAGE")
    ]
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", image) for image in images)
    for image in images:
        assert call("image", "inspect", "--format", "{{.Id}}", image).decode() == image
    with tempfile.TemporaryDirectory(prefix="flow78-nginx-ci-") as temporary:
        result = asyncio.run(exercise(Path(temporary), *images))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
