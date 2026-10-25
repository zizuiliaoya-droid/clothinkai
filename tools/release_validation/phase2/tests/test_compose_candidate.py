import unittest

from contracts import Rejected
import compose_candidate as compose
from tests.fakes import R, baseline


class ComposeTests(unittest.TestCase):
    def test_infrastructure_and_operations_mounts_are_preserved(self):
        base = baseline()["compose"]
        candidate = compose.generate(
            base, "a" * 40, "sha256:" + "a" * 64, "sha256:" + "b" * 64, "dbm.dumb"
        )
        self.assertEqual(candidate["services"]["prod-db"], base["services"]["prod-db"])
        self.assertEqual(
            candidate["services"]["prod-redis"], base["services"]["prod-redis"]
        )
        api = candidate["services"]["prod-api"]
        self.assertIn(R + "/build/control:/opt/production:ro", api["volumes"])
        self.assertIn(R + "/backups:/run/backups:ro", api["volumes"])
        self.assertEqual(
            candidate["services"]["prod-worker"]["healthcheck"], {"disable": True}
        )
        self.assertEqual(api["restart"], "no")

    def test_database_mutation_and_lost_uploader_mount_rejected(self):
        base = baseline()["compose"]
        for role, change in (("prod-db", "image"), ("prod-api", "volumes")):
            candidate = compose.generate(
                base, "a" * 40, "sha256:" + "a" * 64, "sha256:" + "b" * 64, "dbm.dumb"
            )
            candidate["services"][role][change] = []
            with self.assertRaises(Rejected):
                compose.validate(candidate, base, "a" * 40)

    def test_only_explicit_application_services_can_be_selected(self):
        for roles in ((), ("prod-db",), ("prod-api", "prod-redis")):
            with self.assertRaises(Rejected):
                compose.up_plan("a" * 40, roles)
        command = compose.up_plan("a" * 40, ("prod-api", "prod-web"))
        self.assertEqual(
            command[-8:],
            (
                "up",
                "--no-deps",
                "-d",
                "--no-build",
                "--pull",
                "never",
                "prod-api",
                "prod-web",
            ),
        )
