import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from oci.dns.models import ExternalMaster
from oci_dns_batch_common import run_batch


class PreflightTests(unittest.TestCase):
    def response(self, number, compartment='lab', ip='192.0.2.1', key='zone-key'):
        return SimpleNamespace(headers={'etag': 'version-1'}, data=SimpleNamespace(
            id=f'ocid1.dns-zone.oc1..{number}', name=f'zone{number}.test',
            compartment_id=compartment, zone_type='SECONDARY', scope='GLOBAL',
            lifecycle_state='ACTIVE', external_masters=[ExternalMaster(address=ip, tsig_key_id=key)]))

    def invoke(self, folder, client, apply=True):
        ids = Path(folder) / 'ids.txt'
        ids.write_text('ocid1.dns-zone.oc1..1\nocid1.dns-zone.oc1..2\n')
        argv = ['batch', '--compartment-id', 'lab', '--zone-ids-file', str(ids),
                '--old-ip', '192.0.2.1', '--new-ip', '192.0.2.2',
                '--expected-count', '2', '--api-delay', '0', '--output-dir', str(Path(folder) / 'results')]
        if apply:
            argv.append('--apply')
        with patch.object(sys, 'argv', argv), patch('oci_dns_batch_common.oci.config.from_file', return_value={}), \
             patch('oci_dns_batch_common.oci.dns.DnsClient', return_value=client), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_batch('add')

    def test_later_zone_preflight_failure_prevents_all_updates(self):
        client = Mock()
        client.get_zone.side_effect = [self.response(1), self.response(2, compartment='wrong')]
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.invoke(folder, client), 1)
            client.update_zone.assert_not_called()

    def test_preview_never_writes(self):
        client = Mock()
        client.get_zone.side_effect = [self.response(1), self.response(2)]
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.invoke(folder, client, apply=False), 0)
            client.update_zone.assert_not_called()
            records = list((Path(folder) / 'results').glob('*results.jsonl'))
            self.assertEqual([json.loads(line)['status'] for line in records[0].read_text().splitlines()],
                             ['preview', 'preview'])

    def test_concurrent_upstream_change_prevents_write(self):
        client = Mock()
        client.get_zone.side_effect = [self.response(1), self.response(2),
                                       self.response(1, ip='192.0.2.99')]
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.invoke(folder, client), 1)
            client.update_zone.assert_not_called()
