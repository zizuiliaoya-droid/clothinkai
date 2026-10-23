"""Candidate-only Linux adapter surface."""

from contracts import Rejected


def live_factory(configuration):
    raise Rejected("live_launcher_not_delivered")


def integration_blockers():
    return (
        "original_alembic_ci_runtime_not_linux_verified",
        "production_docker_transport_and_complete_collectors_missing",
        "real_pg_sql_timeout_and_cancel_unverified",
        "phase1_dependency_delta_awaits_phase2_independent_review",
        "final_product_archive_images_and_artifacts_unfrozen",
        "continuous_29_step_production_runner_missing",
        "linux_fsync_flock_no_follow_unverified",
        "actual_dbm_timezone_unverified",
        "docker_compose_runtime_unverified",
        "backup_restore_rls_unverified",
        "real_approval_and_final_artifacts_unfrozen",
    )
