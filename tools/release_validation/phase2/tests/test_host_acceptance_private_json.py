import unittest

from contracts import Rejected, canonical
from host_acceptance_control import MAX_BYTES, parse_private_receipt
from sql_policy import SqlRejected, parse


class PrivateReceiptTests(unittest.TestCase):
    def test_complete_set_over_one_mib_is_consumed_without_relaxing_public_parser(self):
        receipt = {
            "mode": "host_observed",
            "rows": [{"id": str(i), "old_row_sha256": "a" * 64} for i in range(15000)],
        }
        raw = canonical(receipt)
        self.assertGreater(len(raw), 1024**2)
        self.assertLess(len(raw), MAX_BYTES)
        self.assertEqual(parse_private_receipt(raw), receipt)
        with self.assertRaises(SqlRejected):
            parse(raw)

    def test_over_limit_duplicate_keys_and_nonfinite_numbers_fail(self):
        for raw in (
            b" " * (MAX_BYTES + 1),
            b'{"x":1,"x":2}',
            b'{"nested":{"x":1,"x":2}}',
            b'{"x":NaN}',
            b'{"x":Infinity}',
            b"[]",
        ):
            with self.subTest(size=len(raw)), self.assertRaises(Rejected):
                parse_private_receipt(raw)
