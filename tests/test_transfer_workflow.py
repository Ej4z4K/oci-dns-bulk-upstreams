"""Offline workflow tests: no OCI or SSH calls."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import oci_dns_transfer_test as engine
from oci.dns.models import ExternalMaster


class TransferWorkflowTests(unittest.TestCase):
    def run_case(self, mode='both', run=True, evidence=True, upstreams=None):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            selection = folder / 'ids.txt'
            selection.write_text('ocid1.dns-zone.oc1..example\n')
            ips = upstreams or (['192.0.2.1', '192.0.2.2'] if mode == 'both' else ['192.0.2.2'])
            zone = Obj(name='lab-01.test', id='ocid1.dns-zone.oc1..example',
                compartment_id='compartment', scope='GLOBAL', zone_type='SECONDARY', lifecycle_state='ACTIVE',
                external_masters=[ExternalMaster(address=ip, port=53, tsig_key_id='key-1') for ip in ips],
                nameservers=[Obj(hostname='ns1.example'), Obj(hostname='ns2.example')])
            events, published = [], {}
            def ssh(args, host, operation, **kwargs):
                events.append(operation)
                if operation == 'axfr':
                    serial, marker = (102, published['marker']) if published else (101, 'baseline')
                    text = f'lab-01.test. 60 IN SOA ns.lab-01.test. hostmaster.lab-01.test. {serial} 120 30 86400 60\nprobe.lab-01.test. 60 IN TXT "{marker}"\n'
                    return {'transfers': [{'zone': zone.name, 'axfr': text}]}
                if operation == 'prepare':
                    return {'prepared': [{'zone': zone.name, 'serial': 102, 'beforeSha256': 'hash'}]}
                if operation == 'cursor':
                    return {'cursor': 'cursor'}
                if operation == 'publish':
                    published.update(marker=kwargs['marker'])
                    return {'backups': ['backup'], 'published': 1}
                if operation == 'notify':
                    return {'notified': 1}
                if operation == 'logs':
                    b = host == '192.0.2.2'
                    line = "client @0x123 192.0.2.50#54321/key tsig.lab-01.test: transfer of 'lab-01.test/IN': AXFR ended: 7 records (serial 102)"
                    return {'messages': [line] if (b and evidence) or (not b and not evidence) else []}
                raise AssertionError(operation)
            argv = ['test', '--zone-ids-file', str(selection), '--compartment-id', 'compartment',
                    '--old-ip', '192.0.2.1', '--new-ip', '192.0.2.2', '--lab-prefix', 'lab',
                    '--expected-count', '1', '--transfer-ips', '192.0.2.50', '--timeout', '1',
                    '--output-dir', str(folder / 'results')]
            if run:
                argv.append('--run')
            with patch.object(sys, 'argv', argv), patch.object(engine.oci.config, 'from_file', return_value={}), \
                 patch.object(engine.oci.dns, 'DnsClient') as client, patch.object(engine, 'ssh', side_effect=ssh), \
                 patch.object(engine, 'query', return_value=[Obj(serial=101)]), \
                 patch.object(engine, 'check', side_effect=lambda z, s, m, n: {'status': 'pass', 'soaSerial': n, 'server': s}), \
                 patch.object(engine.time, 'monotonic', side_effect=[0, 2]), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                client.return_value.get_zone.return_value = Obj(data=zone)
                result = engine.main(mode)
                self.assertFalse(client.return_value.update_zone.called)
            report = json.loads(next((folder / 'results').glob('*.json')).read_text())
            return result, report, events

    def test_preview_never_publishes_or_notifies(self):
        code, report, events = self.run_case(run=False)
        self.assertEqual((code, report['status']), (0, 'preview'))
        self.assertNotIn('publish', events)
        self.assertNotIn('notify', events)

    def test_both_pass_requires_completed_b_transfer_and_fresh_dns(self):
        code, report, events = self.run_case()
        self.assertEqual((code, report['status']), (0, 'pass'))
        self.assertTrue(report['zones'][0]['bTransfers'])
        self.assertEqual(len(report['zones'][0]['dnsChecks']), 2)
        self.assertIn('notify', events)

    def test_a_transfer_only_is_inconclusive(self):
        code, report, _ = self.run_case(evidence=False)
        self.assertEqual((code, report['status']), (2, 'b-transfer-not-observed'))
        self.assertTrue(report['zones'][0]['aTransfers'])
        self.assertFalse(report['zones'][0]['bTransfers'])

    def test_b_only_pass_and_missing_evidence(self):
        for evidence, code, status in ((True, 0, 'pass'), (False, 2, 'b-transfer-not-observed')):
            actual, report, _ = self.run_case(mode='b-only', evidence=evidence)
            self.assertEqual((actual, report['status']), (code, status))

    def test_wrong_phase_stops_before_ssh_or_publishing(self):
        code, report, events = self.run_case(mode='both', upstreams=['192.0.2.2'])
        self.assertEqual((code, report['status']), (1, 'error'))
        self.assertEqual(events, [])
