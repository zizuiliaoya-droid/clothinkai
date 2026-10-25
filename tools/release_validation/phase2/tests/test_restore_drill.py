from copy import deepcopy
import unittest

from contracts import BEFORE, Rejected
import restore_drill as restore
from tests.test_backup_adapter import NAME, SHA


def restored():
    return {
        "container": "clothinkai-flow78-drill-aaaaaaaaaaaa",
        "network_mode": "none",
        "ports": [],
        "data_path": "/srv/clothinkai/production-20261018/releases/flow78-aaaaaaaaaaaa/drill/data",
        "production_mounts": [],
        "dump_sha256": SHA,
        "pg_version": "18.6",
        "head": BEFORE,
        "exit_code": 0,
        "app_superuser": False,
        "app_bypassrls": False,
        "rls_app_checked": True,
        "schema_sha256": "c" * 64,
        "rows_sha256": "d" * 64,
        "acl_sha256": "e" * 64,
        "owners_sha256": "f" * 64,
        "roles_sha256": "1" * 64,
        "references_sha256": "2" * 64,
        "original_db_id": "3" * 64,
        "original_redis_id": "4" * 64,
        "writes_still_paused": True,
    }


class RestoreTests(unittest.TestCase):
    def test_restore_plan_has_no_network_ports_or_prod_data_mount(self):
        result = restore.plan("a" * 40, NAME, "sha256:" + "5" * 64)
        self.assertEqual(result["network"], "none")
        self.assertEqual(result["ports"], [])
        self.assertTrue(result["data_path"].endswith("/drill/data"))
        self.assertFalse(result["automatic_remove"])

    def test_acl_owner_rows_or_constraints_mismatch_rejected(self):
        original = restored()
        for field in (
            "acl_sha256",
            "owners_sha256",
            "schema_sha256",
            "rows_sha256",
            "roles_sha256",
        ):
            observed = deepcopy(original)
            observed[field] = "0" * 64
            with self.assertRaises(Rejected):
                restore.verify(observed, original)

    def test_prod_mount_network_or_app_privilege_rejected(self):
        original = restored()
        for field, value in (
            ("network_mode", "production"),
            ("production_mounts", ["prod-data"]),
            ("app_bypassrls", True),
            ("rls_app_checked", False),
            ("exit_code", None),
        ):
            observed = deepcopy(original)
            observed[field] = value
            with self.assertRaises(Rejected):
                restore.verify(observed, original)

    def test_isolated_receipt_binds_original_infrastructure_and_full_dump(self):
        original = restored()
        accepted = restore.verify(original, original)
        self.assertEqual(accepted["dump_sha256"], SHA)
        changed = deepcopy(original)
        changed["original_db_id"] = "9" * 64
        with self.assertRaises(Rejected):
            restore.verify(changed, original)
