# Disposable Linux release validation

This branch adds an isolated validation workflow. It never deploys a release.
Only pushes to `codex/release-linux-ci-1023` trigger the new workflow; root owns
the push. Existing product workflows and source remain unchanged.

The tool checkout uses the CI commit. The separately checked out product uses
commit `94d2044284785c0e9e4719c54e0ef12548b95fb7`, tree
`dce6b81e9a89aed12bb9eba9047904e3971cd6e1`. These identities must never be replaced
by the later CI-only commit. `verify_bundle.py` checks the exact public file
allowlist and byte hashes before importing the candidate tools.

Root authorized capture of the exact 68-file host successor on 2026-10-24.
`host-bundle-manifest-1024.json` freezes only this disposable validation input.
The original `bundle-manifest.json` and `ci-transfer-pins-1023.json` remain
unchanged historical identities; the complete original 62-file snapshot and
workflow remain recoverable at commit `8817f9a3dac979955729dab4517151f123bdc4eb`.
No previous CI outcome applies automatically to this successor.

Current validation identities:

- Phase1 pins: `bdee6b80fb53c83dda5ed69b6082ada68290c667c33631a120559608cf90f5ba`.
- Exact CI transfer manifest: `c4de17a5086542d0801507fcd4b6f1417b30d3d283b1db95b98a60f1b469385a`.
- Full phase2 audit identity: `2e8dd7e2922c318b31d0d3e664a99585d654f9636d003a064256aced11cf34cb`.

Only the successor transfer manifest's 68 explicitly named members and that
manifest itself were captured, with exact lengths and SHA256 verified before
A-lane source edits. The full audit/history collection is not included. Phase2's historical
`implementation-pins.json` does not describe this snapshot and is not copied.
The original transfer manifest contains local `source_roots` provenance paths;
the CI verifier never uses those paths to locate files. It reads only the
adjacent `phase1/` and `phase2/` directories. Git attributes preserve exact
frozen bytes on Windows and Linux. This snapshot remains pending independent
phase2 review and is not a deployment approval.

Each PG matrix entry has its own fresh GitHub-hosted Ubuntu runner and PG18.6
service. The four scenarios are `success`, `lock_timeout`, `cancel`, and
`advisory_busy`; they run the unchanged source Alembic chain through 066, then
the guarded original 067. The fixed password and database name belong only to
the disposable loopback fixture. No repository secrets or production DSNs are
used. A separate OS job runs real pidfd, flock, fsync, symlink and hardlink
checks without PostgreSQL or installed packages. A second independent OS job
runs `ci_tests/run_host_capture_os.py`: actual fixed helper stdout readback,
100,000-byte oversize rejection against the 65,536-byte cap, timeout and caller
cancellation, followed by `waitpid` checks that no owned child remains. It never
calls host collectors, Docker, systemctl, network, or product code. There are
six jobs in total: two OS jobs plus the four fresh PG scenarios.

The captured busy correction writes a fixed child-side diagnostic only after
actual `pg_try_advisory_lock` returns strictly false. The PG entry requires its
policy/backend/lock/head binding and retains it in `result.json`, along with
`unknown/HOLD`, no accepted proof/post-head, exited child and unreleased host
lock. Generic HOLD alone cannot pass the busy case. The other PG boundaries
remain success to 067, actual 55P03 lock timeout, and cancellation after the
connection/advisory checkpoint. Cancellation does not claim in-flight DDL;
process-only container exit does not claim Docker container cleanup. Replay is
same-process re-entry, not a demonstrated restart/recovery experiment.

Dependencies are installed on the runner from this exact source's pinned
`backend/requirements-dev.txt` and its `requirements.txt` include, preferring
the Tsinghua PyPI mirror. A bounded network/HTTP probe selects official PyPI
only if that mirror is unavailable and records the choice and probe outcome
in `dependency-source.json`. An installation/dependency error fails the job;
it does not trigger another index or mixed `extra-index` resolution.
These source declarations are not a complete transitive hash lock; this CI does
not establish offline wheel/package semantics. No dependencies are installed
locally during preparation.

Artifacts contain only explicit synthetic result JSON, tool/source provenance
JSON, dependency-source selection JSON, and the test entry's stderr output.
No checkout, temporary fixture tree,
backup, credentials, environment dump, or `.agents` directory is uploaded.
Job/step/process timeouts are bounded and matrix fail-fast is disabled so all
four independent scenarios can report their own outcomes.

Successful CI would establish only the covered Linux kernel and PG behavior.
Full production 29-action execution, Docker transport, real backup/R2/restore,
production ACL/RLS/data comparison and deployment readiness remain unverified.
`deployable=false` and `live_enabled=false` remain mandatory.
The pinned product source is solely this executor probe's fixture source; it
is not the final release commit and does not include later PR8 fixes.

Local preparation checks use `python -B tools/release_validation/test_host_bundle.py`
plus Python AST, YAML contract and Git blob checks. They do not run the Linux
or PG cases on Windows. Root owns pushing this exact head, downloading the new
artifacts and independently auditing them. The newly captured host source is
already stale relative to any subsequent A-lane edits: those require a new
allowlist/capture and distinct validation, never inheritance of these results.
