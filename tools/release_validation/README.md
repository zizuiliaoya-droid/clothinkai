# Disposable Linux release validation

This branch adds an isolated validation workflow. It never deploys a release.
Only pushes to `codex/release-linux-ci-1023` trigger the new workflow; root owns
the push. Existing product workflows and source remain unchanged.

The tool checkout uses the CI commit. The separately checked out product uses
commit `94d2044284785c0e9e4719c54e0ef12548b95fb7`, tree
`dce6b81e9a89aed12bb9eba9047904e3971cd6e1`. These identities must never be replaced
by the later CI-only commit. `verify_bundle.py` checks the exact public file
allowlist and byte hashes before importing the candidate tools.

Root approved this exact 62-file snapshot for the disposable test branch on
2026-10-23. `bundle-manifest.json` is frozen for this validation only:

- Phase1 pins: `bdee6b80fb53c83dda5ed69b6082ada68290c667c33631a120559608cf90f5ba`.
- Exact CI transfer manifest: `67fcb9d0e962eafd891b7b86ac00e3b2405513d7b16f5dee8e058dad75a6497a`.
- Full phase2 audit identity: `14874e2d612d60827a86ac03ff3d42935d5b3900e10bae625cff56bfa44513c5`.

Only the transfer manifest's 62 members and that manifest itself are copied.
The full 188-entry audit/history collection is not included. Phase2's historical
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
checks without PostgreSQL or installed packages.

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
