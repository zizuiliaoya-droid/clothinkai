"""Hand-built public synthetic identities, never copies production data."""

from copy import deepcopy

from contracts import BEFORE, TARGET, digest, validate_input
from tests.test_state_machine import input_data

R = "/srv/clothinkai/production-20261018"


def baseline():
    services, containers = {}, {}
    for i, role in enumerate(("db", "redis", "api", "web", "worker", "beat"), 1):
        name = "prod-" + role
        volumes = []
        if role == "db":
            volumes = [R + "/data:/var/lib/postgresql"]
        elif role == "redis":
            volumes = [R + "/redis:/data"]
        else:
            volumes = [R + "/build/control:/opt/production:ro"]
            if role != "web":
                volumes += [
                    R + "/secrets:/run/production:ro",
                    R + "/backups:/run/backups:ro",
                ]
            else:
                volumes += [
                    R + "/acme:/var/www/acme:ro",
                    R + "/letsencrypt:/etc/letsencrypt:ro",
                ]
            if role == "beat":
                volumes += [R + "/beat:/var/lib/celery"]
        services[name] = {
            "image": "sha256:" + str(i) * 64,
            "container_name": "clothinkai-prod-" + role + "-20261018",
            "volumes": volumes,
            "restart": "unless-stopped",
            "networks": {"back": {"aliases": [name]}},
            "healthcheck": {"disable": role in {"worker", "web"}},
        }
        containers[name] = {
            "id": str(i) * 64,
            "image": services[name]["image"],
            "mounts": list(volumes),
            "networks": {"back": "7" * 64},
            "running": True,
            "oom": False,
            "restart_count": 0,
        }
    return {
        "identity": deepcopy(TARGET),
        "schema": BEFORE,
        "pg_version": "18.6",
        "compose": {
            "name": "clothinkai-production-20261018",
            "services": services,
            "networks": {
                "back": {"name": "clothinkai-prod-internal-20261018", "internal": True},
                "egress": {"name": "clothinkai-prod-egress-20261018"},
            },
        },
        "containers": containers,
        "network_ids": {"back": "7" * 64, "egress": "8" * 64},
        "roles": {
            "app_superuser": False,
            "app_bypassrls": False,
            "bypass_superuser": False,
            "bypass_bypassrls": True,
            "owner_expected": True,
        },
        "ops": {
            "uploader_mount": True,
            "uploader_import": True,
            "certbot_image": "sha256:" + "9" * 64,
            "backup_sha256": "a" * 64,
            "renew_sha256": "b" * 64,
            "old_frontend_image": "sha256:" + "4" * 64,
        },
        "external": {"old_jobs_stopped": True, "github_zeabur_hooks_frozen": True},
        "space": {"free_bytes": 20 * 1024**3, "required_bytes": 10 * 1024**3},
        "certificate": {"ip_match": True, "remaining_seconds": 172800},
    }


def spec_for(base):
    return validate_input({**input_data(), "baseline_sha256": digest(base)})
