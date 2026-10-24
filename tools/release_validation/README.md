# Disposable Linux release validation

This branch only prepares validation. Root owns pushing its exact commit and auditing the resulting artifacts. It does not deploy or complete the 29-action host release.

The current input is the 101-file public checkpoint captured by root before A resumed editing. Only .agents/tasks/20261008-wave2/release-host-ci-capture-1024/ was read for this capture, never the later mutable source. checkpoint-ci-transfer-pins-1024.json has SHA256 4b9c0ccb2ff0e469ebb63864333060bd84611f7394fa3a1a1185417f084cb909; checkpoint-bundle-manifest-1024.json freezes its exact allowlist/lengths/hashes. Both new manifests and all phase members use Git -text attributes, so local bytes and Git/Linux bytes must agree. The verifier uses only adjacent phase1/phase2 paths, never the manifest's historical source_roots.

Current product fixture source is 55477c44f09bb1a25169bfb44ff6c90bc4f9cedb, tree 28c0cac9a15cc48a5dfa5c7d3ed732cd6a59abdc. It is checked out independently of the CI tool commit. This is an unmerged candidate fixture, not final main, production identity or full release-package acceptance. Phase1 package pins are 0cc34f4847dce14a3ffc7868f20b37a32500144a5895563228b9d84a66bbc0c0; original SQL guard pins remain bdee6b80fb53c83dda5ed69b6082ada68290c667c33631a120559608cf90f5ba. The package/scanner successor does not relabel prior phase1 reviews.

Historical evidence remains unchanged: original 62-file bundle/transfer at 8817f9a3dac979955729dab4517151f123bdc4eb; host 68-file transfer c4de17a5086542d0801507fcd4b6f1417b30d3d283b1db95b98a60f1b469385a at c940fc471f7de4c4063c8899dd5f02e733da47ea. The old host-bundle-manifest-1024.json local Windows SHA was fe7d4357ad4793d800671b5b3b9df39749a5e2f977949549375e250278d359b2; its committed Git/Linux LF identity is 72dd7ff71895a9f61d10f1b0d8738f6861b00ffc4fcf9fa7b47e7ca4e7e348ba. Root established the difference is solely CRLF/LF. Those old manifests are retained, and the complete prior source/workflow remains at the prior commits. Old CI green results never automatically cover new source.

Nine jobs run on separate GitHub-hosted Ubuntu runners:

- Original Linux OS kernel: pidfd/flock/fsync and link checks in temporary paths.
- Original host capture kernel: bounded fixed helper stdout, 100000-byte overflow, timeout/cancellation and waitpid child reaping. No host collectors.
- Web kernel: temporary nginx-active file atomic replacement/fsync, stale original rejection, pending/active symlink and hardlink denial. It does not reload real nginx or access host paths.
- Build kernel: temporary nested staging bytes/modes/fsync, exclusive no-overwrite and symlink target denial. It does not invoke Docker or build an image.
- Checkpoint contracts: 40 ci_tests using synthetic source Git fixtures, then 22 host policy/journal/finite operations/build/migration tests in a separate process. The host suite uses the existing real-process/socket denial guards and NoSocketAsyncCase. Green here proves the finite checkpoint contracts, not actual Docker migration/build or production readiness.
- Four isolated PostgreSQL 18.6 scenarios: success, lock_timeout, cancel and advisory_busy. Each uses a fresh service and unchanged original source Alembic chain through 066, then guarded 067. The fixed fixture password/database and prod-db -> 127.0.0.1 mapping are runner-local. No repository secrets/production DSNs are used.

The busy case still requires the exact child callback diagnostic after real pg_try_advisory_lock returns false, with policy/backend/lock/head binding, HOLD, no accepted proof/post-head, exited child and unreleased host lock. Generic HOLD does not pass. Cancellation occurs after connection/advisory checkpoint, not proven in-flight DDL; process-only exit is not Docker container cleanup. Same-process replay tests do not establish restart/recovery.

PG dependencies are installed only on the runner from the exact fixture source declarations. A bounded probe selects Tsinghua PyPI first, official PyPI only if the probe fails, and records the choice. Dependency/installation errors never trigger another index or mixed extra-index resolution. These declarations are not a transitive hash lock, and CI does not establish offline package semantics. No local installation is part of preparation.

Only explicit result/provenance/dependency-selection JSON and synthetic test logs are uploaded, never checkouts, fixture trees, secrets, config, backups or .agents. Per-job/step/process timeouts remain bounded. Matrix fail-fast is false. deployable=false, live_enabled=false and production_29_actions_verified=false are recorded. The current BACKUP/RESTORE implementation is deliberately absent from this frozen input. Later A/B edits need another capture and new evidence.

Local checks: python -B tools/release_validation/test_host_bundle.py; from phase2, the two contract commands listed by the transfer manifest. Python AST, YAML invariants and exact Git blob checks complement them. Actual Linux kernel and PG cases are not run on Windows. Real 29-action host closure, real Docker builds/migration, backup/R2/restore, production comparison/approvals and release readiness remain pending.
