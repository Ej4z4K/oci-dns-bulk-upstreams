import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from oci.dns.models import ExternalMaster
from oci_dns_batch_common import desired_masters, selected_ids, secure_file


class PortabilityAndRemovalTests(unittest.TestCase):
    def test_windows_utf8_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ids.txt'
            path.write_bytes(b'\xef\xbb\xbf# reviewed\r\nocid1.dns-zone.oc1..one\r\n\r\nocid1.dns-zone.oc1..two\r\n')
            self.assertEqual(selected_ids(path), ['ocid1.dns-zone.oc1..one', 'ocid1.dns-zone.oc1..two'])

    def test_remove_rejects_mismatched_zone_key(self):
        before = [ExternalMaster(address='192.0.2.1', tsig_key_id='a'),
                  ExternalMaster(address='192.0.2.2', tsig_key_id='b')]
        with self.assertRaises(ValueError):
            desired_masters(before, '192.0.2.1', '192.0.2.2', 'remove')

    def test_output_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'log.txt'
            with secure_file(path) as stream:
                stream.write('retained evidence\n')
            with self.assertRaises(FileExistsError):
                secure_file(path)
            self.assertEqual(path.read_text(), 'retained evidence\n')
