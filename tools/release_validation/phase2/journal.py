"""Append/fsync journal within an explicit synthetic sandbox, never a production path."""

from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import tempfile

from contracts import Rejected, canonical, digest, exact, parse_json, require


def sandbox_root(path):
    root = Path(path).absolute()
    require(
        root.name.startswith("flow78-phase2-fixture-")
        and root.is_relative_to(Path(tempfile.gettempdir()).resolve()),
        "synthetic_path",
    )
    for part in (root, *root.parents):
        require(not part.is_symlink() and not part.is_junction(), "path_link")
    require(root.is_dir(), "sandbox_missing")
    return root


def regular(path):
    require(not path.is_symlink() and not path.is_junction(), "file_link")
    if path.exists():
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "file_regular")


def sync_parent(path):
    if os.name == "nt":
        # File fsync is real; Windows directory-fsync is deliberately unverified.
        return False
    handle = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
    return True


class Journal:
    def __init__(self, root, fail_at=None):
        self.root = sandbox_root(root)
        self.path = self.root / "journal.jsonl"
        self.lockpath = self.root / "release.lock"
        self.fail_at = fail_at
        self.committed = False
        self.lock = None
        self.directory_fsync_verified = False

    def append(self, kind, payload):
        require(kind in {"intent", "result", "hold"}, "journal_kind")
        self.committed = False
        acquired = self.lock is None
        if acquired:
            self.acquire()
        try:
            rows = self.read()
            item = {
                "sequence": len(rows) + 1,
                "previous": rows[-1]["sha256"] if rows else "0" * 64,
                "kind": kind,
                "payload": payload,
                "utc": datetime.now(timezone.utc).isoformat(),
                "mode": "synthetic",
            }
            item["sha256"] = digest(item)
            encoded = canonical(item) + b"\n"
            require(len(encoded) <= 1024 * 1024, "journal_record_limit")
            regular(self.path)
            with self.path.open("ab") as output:
                if self.fail_at == "disk_full":
                    raise Rejected("disk_full")
                output.write(encoded)
                output.flush()
                if self.fail_at == "file_fsync":
                    raise Rejected("file_fsync")
                os.fsync(output.fileno())
            if self.fail_at == "directory_fsync":
                raise Rejected("directory_fsync")
            self.directory_fsync_verified = sync_parent(self.root)
            self.committed = True
            return item
        finally:
            if acquired:
                self.release()

    def read(self):
        regular(self.path)
        if not self.path.exists():
            return []
        require(self.path.stat().st_size <= 16 * 1024**2, "journal_size")
        raw = self.path.read_bytes()
        require(not raw or raw.endswith(b"\n"), "journal_truncated")
        rows, previous = [], "0" * 64
        for line in raw.splitlines():
            item = parse_json(line)
            exact(
                item,
                ("sequence", "previous", "kind", "payload", "utc", "mode", "sha256"),
            )
            require(
                item["sequence"] == len(rows) + 1
                and type(item["sequence"]) is int
                and item["previous"] == previous
                and item["mode"] == "synthetic",
                "journal_chain",
            )
            require(
                item["sha256"]
                == digest({k: v for k, v in item.items() if k != "sha256"}),
                "journal_hash",
            )
            require(item["kind"] in {"intent", "result", "hold"}, "journal_kind")
            previous = item["sha256"]
            rows.append(item)
        return rows

    def acquire(self):
        require(self.lock is None, "lock_already_owned")
        regular(self.lockpath)
        handle = self.lockpath.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.lockpath.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise Rejected("lock_busy") from None
        self.lock = handle

    def release(self):
        if self.lock is not None:
            if os.name == "nt":
                import msvcrt

                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None

    def atomic_artifact(self, basename, raw):
        require(
            basename
            in {
                "evidence.json",
                "nginx-active.conf",
                "old-active.conf",
                "old-active.metadata.json",
            },
            "artifact_name",
        )
        target, temporary = self.root / basename, self.root / (basename + ".pending")
        regular(target)
        regular(temporary)
        require(not target.exists() and not temporary.exists(), "artifact_exists")
        with temporary.open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        if self.fail_at == "before_replace":
            raise Rejected("replace_interrupted")
        os.replace(temporary, target)
        self.directory_fsync_verified = sync_parent(self.root)
        return target
