import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from oci.dns.models import ExternalMaster
from oci_dns_batch_common import desired_masters, fingerprint, selected_ids


class BatchSafetyTests(unittest.TestCase):
    def master(self, ip, key=None, port=None):
        return ExternalMaster(address=ip, tsig_key_id=key, port=port)

    def test_add_preserves_unrelated_master_and_inherits_zone_key(self):
        old = self.master("192.0.2.1", "zone-one-key", 53)
        other = self.master("192.0.2.3", "other-key", 53)
        result, changed = desired_masters([old, other], "192.0.2.1", "192.0.2.2", "add")
        self.assertTrue(changed)
        self.assertIs(result[0], old)
        self.assertIs(result[1], other)
        self.assertEqual(result[2].tsig_key_id, "zone-one-key")
        self.assertEqual(result[2].port, 53)
        zone_two, _ = desired_masters([self.master("192.0.2.1", "zone-two-key")],
                                     "192.0.2.1", "192.0.2.2", "add")
        self.assertEqual(zone_two[1].tsig_key_id, "zone-two-key")

    def test_add_rejects_unsigned_old_or_wrong_new_key(self):
        for masters in ([self.master("192.0.2.1")],
                        [self.master("192.0.2.1", "a"), self.master("192.0.2.2", "b")]):
            with self.assertRaises(ValueError):
                desired_masters(masters, "192.0.2.1", "192.0.2.2", "add")

    def test_remove_requires_signed_new_and_preserves_others(self):
        old = self.master("192.0.2.1", "a")
        for masters in ([old], [old, self.master("192.0.2.2")]):
            with self.assertRaises(ValueError):
                desired_masters(masters, "192.0.2.1", "192.0.2.2", "remove")
        new, other = self.master("192.0.2.2", "a"), self.master("192.0.2.3", "c")
        result, changed = desired_masters([old, new, other], "192.0.2.1", "192.0.2.2", "remove")
        self.assertTrue(changed)
        self.assertEqual(result, [new, other])
        repeated, changed = desired_masters(result, "192.0.2.1", "192.0.2.2", "remove")
        self.assertFalse(changed)
        self.assertEqual(repeated, result)

    def test_default_port_readback_normalizes_to_53(self):
        self.assertEqual(fingerprint([self.master("192.0.2.1", "a")]),
                         fingerprint([self.master("192.0.2.1", "a", 53)]))

    def test_selection_rejects_duplicate_and_non_zone_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "zones.txt"
            for content in ("ocid1.dns-zone.oc1..a\nocid1.dns-zone.oc1..a\n", "not-a-zone\n"):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    selected_ids(path)


if __name__ == "__main__":
    unittest.main()
