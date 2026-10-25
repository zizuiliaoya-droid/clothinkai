"""Operator-provisioned fixed input -> one finite host release, never issue approval."""

import argparse
import asyncio
import io
import json
import re
import tarfile
from pathlib import Path

from contracts import canonical, exact, require
from fixed_host_transport import ROOT
from host_release_input import HostReleaseSpec, validate_release
from host_sequence import HostCoordinator, HostDirectory, HostJournal
from phase1_package_binding import BoundPackage, tool_bundle
from sql_policy import parse, sha


def hydrate(archive, evidence, binding):
    """Recheck approved immutable bytes; Git semantic review happened at packing.

    Evidence and every payload byte are independently hashed in the approved
    HostReleaseSpec. This loader cannot grant semantic approval to a new archive.
    """
    require(
        sha(archive) == binding["package_sha256"]
        and sha(evidence) == binding["package_evidence_sha256"],
        "runner_package_binding",
    )
    files, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as handle:
        for item in handle:
            require(
                item.isfile()
                and item.name not in files
                and re.fullmatch(r"[A-Za-z0-9_./-]+", item.name)
                and all(v not in ("", ".", "..") for v in item.name.split("/"))
                and item.size <= 16 * 1024**2,
                "runner_archive_member",
            )
            total += item.size
            require(
                len(files) < 10000 and total <= 256 * 1024**2, "runner_archive_bound"
            )
            stream = handle.extractfile(item)
            require(stream is not None, "runner_archive_stream")
            raw = stream.read(item.size + 1)
            require(len(raw) == item.size, "runner_archive_short_read")
            files[item.name] = raw
    manifest = parse(files.pop("release-manifest.json"))
    require(
        manifest["files"]
        == {n: {"bytes": len(b), "sha256": sha(b)} for n, b in files.items()},
        "runner_manifest_bytes",
    )
    return BoundPackage(evidence, tuple(sorted(files.items())))


def load(commit):
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "runner_commit")
    require(
        Path(__file__).resolve().parent
        == Path(ROOT) / "releases" / ("flow78-" + commit[:12]) / "bootstrap",
        "runner_fixed_location",
    )
    directory = HostDirectory(commit)
    try:
        packed = parse(directory.read("host-input.json", 1024 * 1024))
        exact(packed, ("data", "binding", "baseline"))
        require(packed["data"]["source_commit"] == commit, "runner_source_commit")
        archive = directory.read("candidate.tar.gz", 256 * 1024**2)
        evidence = directory.read("package-evidence.json", 1024 * 1024)
    finally:
        directory.close()
    package = hydrate(archive, evidence, packed["binding"])
    spec = validate_release(
        HostReleaseSpec(
            canonical(packed["data"]),
            canonical(packed["binding"]),
            package,
            tuple(sorted(tool_bundle().items())),
        )
    )
    return spec, packed["baseline"]


async def run(commit):
    spec, baseline = load(commit)
    journal = HostJournal(spec)
    coordinator = HostCoordinator(spec, journal, baseline)
    while True:
        result = await coordinator.run_next()
        print(json.dumps(result, sort_keys=True), flush=True)
        if result["status"] == "COMPLETE":
            journal.close_completed_claim(spec)
            journal.release()
            journal.directory.close()
            return 0
        if result["status"] != "STEP_RECORDED":
            # OS flock dies with the process, but unresolved journal and the
            # immutable host-wide claim remain. No clean-up or retry is inferred.
            return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args.commit))
    except BaseException:
        print('{"status":"HOST_RELEASE_STOP","details":"private_journal_only"}')
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
