"""Finite Linux mechanisms. Only disposable loopback CI execution is reachable.

Production command descriptions are closed, but have no execution entry. No
caller-provided argv, shell, SQL, config path or production activation flag.
"""

import asyncio
import os
import re
import signal
import socket
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from sql_policy import LinuxCISpec, canonical, need, parse, sha, validate_spec


def ci_root(nonce):
    need(type(nonce) is str and re.fullmatch("[0-9a-f]{32}", nonce), "ci_nonce")
    return Path("/tmp/flow78-linux-ci-" + nonce)


def require_linux():
    need(
        sys.platform == "linux"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "pidfd_open"),
        "linux_required",
    )


def verify_ci_network():
    """CI must map this exact product DNS name to a loopback-only PG service."""
    require_linux()
    rows = socket.getaddrinfo("prod-db", 5432, type=socket.SOCK_STREAM)
    need(
        bool(rows) and all(row[4][0] == "127.0.0.1" for row in rows),
        "ci_loopback_required",
    )


def docker_command(action):
    commands = {
        "inspect_old_api": (
            "/usr/bin/docker",
            "inspect",
            "clothinkai-prod-api-20261018",
        ),
        "inspect_old_worker": (
            "/usr/bin/docker",
            "inspect",
            "clothinkai-prod-worker-20261018",
        ),
        "inspect_old_beat": (
            "/usr/bin/docker",
            "inspect",
            "clothinkai-prod-beat-20261018",
        ),
        "inspect_db": ("/usr/bin/docker", "inspect", "clothinkai-prod-db-20261018"),
        "inspect_redis": (
            "/usr/bin/docker",
            "inspect",
            "clothinkai-prod-redis-20261018",
        ),
        "stop_old_api": (
            "/usr/bin/docker",
            "stop",
            "--time",
            "120",
            "clothinkai-prod-api-20261018",
        ),
        "stop_old_worker": (
            "/usr/bin/docker",
            "stop",
            "--time",
            "120",
            "clothinkai-prod-worker-20261018",
        ),
        "stop_old_beat": (
            "/usr/bin/docker",
            "stop",
            "--time",
            "120",
            "clothinkai-prod-beat-20261018",
        ),
    }
    need(type(action) is str and action in commands, "closed_docker_action")
    return commands[action]


class PrivateDirectory:
    """Dirfd operations: reject symlinks, hardlinks, unsafe ownership/modes."""

    def __init__(self, nonce):
        require_linux()
        self.root = ci_root(nonce)
        # /tmp itself is an OS-owned sticky directory; the nonce directory is private.
        parent = os.open("/tmp", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.fd = os.open(
                self.root.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
        finally:
            os.close(parent)
        info = os.fstat(self.fd)
        need(
            info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "ci_directory_owner_mode",
        )

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _parent(self, name):
        need(
            type(name) is str
            and re.fullmatch("[A-Za-z0-9_./-]+", name)
            and not name.startswith("/")
            and all(p not in ("", ".", "..") for p in name.split("/")),
            "relative_file",
        )
        parts = name.split("/")
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                new = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = new
                info = os.fstat(fd)
                need(
                    info.st_uid == os.geteuid() and not info.st_mode & 0o022,
                    "nested_directory",
                )
            return fd, parts[-1]
        except BaseException:
            os.close(fd)
            raise

    def open(self, name, flags, mode=0o600):
        parent, base = self._parent(name)
        try:
            fd = os.open(
                base, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent
            )
        finally:
            os.close(parent)
        info = os.fstat(fd)
        if not (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
            and not info.st_mode & 0o077
        ):
            os.close(fd)
            need(False, "private_regular_file")
        return fd

    def read(self, name, maximum=16 * 1024**2):
        fd = self.open(name, os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(maximum + 1)
        need(len(raw) <= maximum, "file_size")
        return raw

    def create(self, name, raw):
        fd = self.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        parent, _base = self._parent(name)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)


class LinuxJournal:
    """Same intent/result chain as the existing journal, on protected Linux dirfds."""

    def __init__(self, nonce):
        self.directory = PrivateDirectory(nonce)
        self.root = self.directory.root
        self.lock = None

    def acquire(self):
        import fcntl

        need(self.lock is None, "lock_owned")
        fd = self.directory.open("release.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self.lock = fd
        os.fsync(fd)
        os.fsync(self.directory.fd)

    def release(self):
        import fcntl

        if self.lock is not None:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            os.close(self.lock)
            self.lock = None

    def read(self):
        try:
            raw = self.directory.read("journal.jsonl")
        except FileNotFoundError:
            return []
        need(not raw or raw.endswith(b"\n"), "journal_truncated")
        rows, previous = [], "0" * 64
        for line in raw.splitlines():
            row = parse(line)
            need(
                row.get("sequence") == len(rows) + 1
                and type(row["sequence"]) is int
                and row.get("previous") == previous
                and row.get("mode") == "linux_ci"
                and row.get("kind") in ("intent", "result", "hold")
                and row.get("sha256")
                == sha(canonical({k: v for k, v in row.items() if k != "sha256"})),
                "journal_chain",
            )
            previous = row["sha256"]
            rows.append(row)
        return rows

    def append(self, kind, payload):
        need(
            self.lock is not None and kind in ("intent", "result", "hold"),
            "journal_lock_required",
        )
        rows = self.read()
        row = {
            "sequence": len(rows) + 1,
            "previous": rows[-1]["sha256"] if rows else "0" * 64,
            "kind": kind,
            "payload": payload,
            "mode": "linux_ci",
            "utc": datetime.now(timezone.utc).isoformat(),
        }
        row["sha256"] = sha(canonical(row))
        raw = canonical(row) + b"\n"
        need(len(raw) <= 1024**2, "journal_record_size")
        fd = self.directory.open(
            "journal.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND
        )
        with os.fdopen(fd, "ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(self.directory.fd)
        return row


class OwnedChild:
    def __init__(self, process, nonce):
        self.process = process
        self.pidfd = os.pidfd_open(process.pid)
        self.child_id = "linux-child-" + str(process.pid)
        self.container_id = "ci-process-" + nonce

    @property
    def returncode(self):
        return self.process.returncode

    async def wait(self):
        return await self.process.wait()

    def terminate(self):
        signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)

    def kill(self):
        signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)


class LinuxCITransport:
    execution_mode = "linux_ci"

    def __init__(self, spec, journal):
        need(
            type(spec) is LinuxCISpec and type(journal) is LinuxJournal,
            "ci_transport_type",
        )
        self.spec = validate_spec(spec)
        self.journal = journal
        need(journal.root == ci_root(spec.data["nonce"]), "ci_journal_binding")
        self.child = None
        self.backend_absent = False

    async def acquire_host_lock(self, spec):
        need(spec == self.spec and self.journal.lock is None, "ci_lock_binding")
        self.journal.acquire()
        need(not self.journal.read(), "prior_execution_no_replay")
        # Durable intent is made before creating a process, including failed spawn.
        self.journal.append("intent", {"name": "MIGRATE", "policy_sha256": spec.sha256})
        info = os.fstat(self.journal.lock)
        self.token = "linux-update-" + str(info.st_dev) + "-" + str(info.st_ino)
        return {
            "policy_sha256": spec.sha256,
            "token": self.token,
            "exclusive": True,
            "kind": "host-update-lock",
        }

    async def spawn(self, spec, host_lock):
        need(
            spec == self.spec
            and host_lock["token"] == self.token
            and self.child is None,
            "spawn_once",
        )
        verify_ci_network()
        from linux_alembic_runtime import verify_staged_files

        verify_staged_files(spec, self.journal.directory)
        need(
            self.journal.directory.read("policy.json")
            == canonical(
                {
                    "data": spec.data,
                    "artifacts": {k: v.decode() for k, v in spec._artifacts},
                }
            ),
            "staged_policy",
        )
        self.child = OwnedChild(
            await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                str(self.journal.root / "tools/linux_alembic_runtime.py"),
                spec.data["nonce"],
                cwd=self.journal.root / "app/backend",
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            ),
            spec.data["nonce"],
        )
        return self.child

    async def collect(self, child, spec):
        need(child is self.child and child.returncode is not None, "collect_owned_exit")
        try:
            result = parse(self.journal.directory.read("result.json", 1024**2))
            if result.get("failure") is not None:
                from linux_collectors import schema_head

                result["post_head"] = await schema_head(spec)
            return result
        except FileNotFoundError:
            from linux_collectors import schema_head

            proof = parse(self.journal.directory.read("connected.json", 1024**2))
            return {
                "policy_sha256": spec.sha256,
                "identity": proof["identity"],
                "proof": proof,
                "post_head": await schema_head(spec),
                "failure": {"code": "execution_failed", "sqlstate": None},
            }

    async def inspect_container(self, child, spec):
        need(child is self.child and child.returncode is not None, "ci_child_alive")
        # Explicit process-only scope; this does not attest any Docker container.
        return {
            "policy_sha256": spec.sha256,
            "container_id": child.container_id,
            "state": "not_applicable",
            "after_process_exit": True,
        }

    async def inspect_backend(self, identity, spec):
        from linux_collectors import inspect_ci_backend

        observed = await inspect_ci_backend(identity, spec)
        self.backend_absent = (
            observed["state"] == "absent" and observed["owned_lock_absent"]
        )
        return observed

    async def release_host_lock(self, host_lock, spec):
        need(
            self.backend_absent
            and self.child.returncode is not None
            and host_lock["token"] == self.token,
            "owned_exit_unknown",
        )
        self.journal.append(
            "result",
            {
                "name": "MIGRATE",
                "policy_sha256": spec.sha256,
                "owned_backend_absent": True,
                "process_exit": self.child.returncode,
            },
        )
        self.journal.release()
        os.close(self.child.pidfd)
        return True
