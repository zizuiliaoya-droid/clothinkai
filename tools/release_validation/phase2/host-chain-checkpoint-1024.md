# Host chain checkpoint 1024

This is a new implementation checkpoint, not production approval. All 29 names have finite host dispatch entries. Production actions executed: **0/29**. No local Docker, production IO, cloud/network operation, package install or secret read occurred in this lane. Windows tests replace OS/container/SQL boundaries; they do not prove the host sequence.

`host_runner.py --commit <40-hex>` is the fixed operator entry. Its required location is `/srv/clothinkai/production-20261018/releases/flow78-<commit12>/bootstrap/host_runner.py`. The operator must provision root-owned private bootstrap/state directories and the reviewed public code, then supply `state/host-input.json`, `candidate.tar.gz`, `package-evidence.json`, `approval.json`, `manual-freeze.json`, `runtime-config.json`, and `migrate-permit.json`. No issuer is implemented. The loader rechecks archive/evidence/all-member/tools hashes against the reviewed input; it does not redo the workstation Git semantic review or authorize a new archive. Run under a clean root environment with Python `-B`; no caller-selected argv, URL, SQL, path or environment override is accepted.

The current candidate is commit `55477c44f09bb1a25169bfb44ff6c90bc4f9cedb`, tree `28c0cac9a15cc48a5dfa5c7d3ed732cd6a59abdc`. The existing phase1 candidate archive remains candidate-only; it is not a final reviewed updater package. Phase1 package pins are `0cc34f4847dce14a3ffc7868f20b37a32500144a5895563228b9d84a66bbc0c0`; the unchanged SQL runtime guard retains historical pins `bdee6b80fb53c83dda5ed69b6082ada68290c667c33631a120559608cf90f5ba`.

Implemented chain groups:

- PREPARE/build: exclusive staging, original offline contexts, no pull/network build, image content and source checks.
- PREFLIGHT/freeze: original full IDs, exact mounts/networks/public compose, original unit bytes, manual-entry attestation, atomic maintenance and old nginx worker drain, original beat stop/read-only DBM snapshot.
- FREEZE_PRODUCERS/DRAIN/FREEZE_VERIFY: fixed single-process uvicorn launch, cgroup v2/PID namespace/start-tick/thread binding, pidfd SIGSTOP, no automatic SIGCONT. Original API stays available for separate fixed docker exec. Uncovered processes, children or thread drift reject. Celery active/reserved/scheduled, all four priority queues and unacked sets must empty in two windows at least 30 seconds apart, within 600 seconds.
- BACKUP/RESTORE: B's original host backup entry, original API uploader, full dump/sidecar/remote receipt and isolated PG restore. Before/after freeze checks now also require the API process group to remain stopped. The pre-migration schema receipt hash is part of the BACKUP journal result and `backup-observed.json`.
- STOP_API/MIGRATE/VERIFY_SCHEMA: terminate the frozen API with SIGKILL without SIGCONT, verify exit 137 and absent original process, then require every connection of all three business roles absent. One owned migration container uses original env.py/SQL guard; independent readback requires exact backend absence, advisory lock absence and target head. Database UTC and monotonic start/end bounds are recorded for acceptance.
- Four app replacement/start: exact public original compose bytes 5800/SHA `5b23a8f8d87d0a044db6a41f97b3eef23cd5fd045e23badcb23861ae31b7bb09`; all existing networks declared external; compose creates only one selected application with no dependencies/build/pull and no start. New immutable full ID is recorded before start. DB/Redis identity remains unchanged. Existing original control/backup/config/beat mounts remain.
- Acceptance/jobs/open: B's actual finite SQL/role/RLS/financial projections, real product readiness shape, worker task registration, original beat format/store and seven-task structure, atomic open plus TLS page-byte/health/readiness/board/ACME observations.
- Restore/final: only original restart policies and original enabled/active/masked timer states are restored. Original uploader import and certbot image/renew script preservation are checked. Two final windows are required. New K7-4/R33 samples and an independent external vantage remain explicitly pending, even when 29 technical actions are recorded.

Host-wide `flow78-release.lock` is acquired before the durable exclusive `flow78-active.json` claim. The per-release journal lock is additional. Process death releases OS flocks, **not** the durable claim or journal HOLD. A different candidate cannot bypass a prior claim. Success removes the claim only while both locks are held and all 29 journal-bound facts are revalidated; unknown results retain it.

The original compose generator reproduces the observed 5800-byte file exactly, without reading environment values. The prior synthetic compose assumed an explicit healthcheck key for API/web; actual compose omits it, so preservation now compares optional keys. The open nginx template now preserves HTTPS ACME challenge routing as well as HTTP; this changes its hash beyond the captured 101-file checkpoint.

The private acceptance receipt parser now accepts bounded complete-set JSON up to 16 MiB, rejects duplicate keys/NaN/Infinity and leaves shared `sql_policy.parse` at 1 MiB. B's original module/pins remain in `baselines/acceptance-9d0138-1024`; the new parser delta has separate pins.

## Session contract decision

The root's safe readonly observation found clothing_app idle=4 and clothing_bypass idle=1. DRAIN/FREEZE_VERIFY/BACKUP/RESTORE retain actual idle and total counts; after entry/task producers are frozen they require active=0, idle-in-transaction (including aborted)=0 and unknown=0, for two >=30-second windows. They do not report total=0. STOP_API through migration require total=0 for clothing_app, clothing_bypass and clothing_archiver, including idle. No role alteration or backend termination is introduced.

## Outstanding proof

The earlier captured 101-file CI applies only to that exact checkpoint. It does not cover this delta. New root lock/claim and Docker producer fixture entry points are present but were **not run on Windows**. Real backup/restore SQL/init/RLS, DBM formats, nginx/proc behavior, application replacement, new finite host journal integration and B's original SQL collectors require disposable Linux/PG/Docker validation. The original R2 upload requires the separately authorized real environment; no credentials or successful upload are invented. Formal independent review and final package assembly remain pending.

The SIGSTOP collector deliberately rejects any other cgroup process, including an in-flight Docker healthcheck exec. The disposable test must characterize healthcheck overlap before deployment; any unknown production observation remains HOLD, not a successful freeze. This is a validation limitation, not permission to skip the process-group check.

See `host-linux-docker-fixtures-1024.md` and `host-pre066-recovery-runbook-1024.md` for the finite remaining validation/recovery boundaries.
