"""Prevent stale or unrelated BIND activity being reported as OCI transfer proof."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from oci.dns.models import ExternalMaster
from oci_dns_transfer_test import completed_transfers, axfr_data, validate_zone
from oci_dns_transfer_remote import prepare_content


class TransferEvidenceTests(unittest.TestCase):
    zone = 'lab-01.test'
    key = 'tsig.lab-01.test.'
    client = '192.0.2.50'
    prefix = "client @0x123 192.0.2.50#54321/key tsig.lab-01.test: transfer of 'lab-01.test/IN': "

    def evidence(self, messages):
        return completed_transfers(messages, self.zone, 102, self.key, {self.client})

    def test_completed_axfr_with_new_serial(self):
        messages = [self.prefix + 'AXFR started (serial 102)',
                    self.prefix + 'AXFR ended: 1 messages, 7 records (serial 102)']
        self.assertEqual(self.evidence(messages), messages[1:])

    def test_completed_ixfr_with_from_to_serial(self):
        messages = [self.prefix + 'IXFR started (serial 101 -> 102)',
                    self.prefix + 'IXFR ended: 2 messages, 8 records']
        self.assertEqual(self.evidence(messages), messages[1:])

    def test_old_serial_started_only_notify_and_incoming_do_not_pass(self):
        for messages in ([self.prefix + 'AXFR ended: 7 records (serial 101)'],
                         [self.prefix + 'AXFR started (serial 102)'],
                         ['zone lab-01.test/IN: sending notifies (serial 102)'],
                         ["transfer of 'lab-01.test/IN' from 10.66.0.10#53: Transfer completed: (serial 102)"]):
            self.assertEqual(self.evidence(messages), [])

    def test_wrong_client_zone_key_or_unsigned_does_not_pass(self):
        line = self.prefix + 'AXFR ended: 7 records (serial 102)'
        for bad in (line.replace('192.0.2.50', '192.0.2.51'),
                    line.replace("'lab-01.test/IN'", "'lab-02.test/IN'"),
                    line.replace('tsig.lab-01.test:', 'tsig.lab-02.test:'),
                    line.replace('/key tsig.lab-01.test', '')):
            self.assertEqual(self.evidence([bad]), [])

    def test_interleaved_clients_cannot_share_start_serial(self):
        a = self.prefix + 'AXFR started (serial 102)'
        b = (self.prefix + 'AXFR ended: 7 records').replace('#54321', '#54322')
        self.assertEqual(self.evidence([a, b]), [])

    def test_parse_signed_axfr_requires_expected_origin_probe_and_soa(self):
        text = ('lab-01.test. 60 IN SOA ns.lab-01.test. hostmaster.lab-01.test. 102 120 30 86400 60\n'
                'probe.lab-01.test. 60 IN TXT "new-marker"\n')
        self.assertEqual(axfr_data({'zone': self.zone, 'axfr': text}), (102, ['new-marker']))
        with self.assertRaises(ValueError):
            axfr_data({'zone': self.zone, 'axfr': text.splitlines()[0] + '\n'})

    def test_prepare_increments_current_serial_and_changes_one_probe(self):
        original = '$TTL 60\n@ IN SOA ns.lab.test. hostmaster.lab.test. (4294967295 120 30 86400 60)\nprobe IN TXT "old"\n'
        changed, serial = prepare_content(original, 'unique-marker')
        self.assertEqual(serial, 0)
        self.assertIn('(0 120 30 86400 60)', changed)
        self.assertIn('probe IN TXT "unique-marker"', changed)
        for bad in (original.replace('probe IN TXT', 'other IN TXT'), original + 'probe IN TXT "duplicate"\n'):
            with self.assertRaises(ValueError):
                prepare_content(bad, 'unique-marker')

    def zone_object(self, ips=('192.0.2.1', '192.0.2.2')):
        return SimpleNamespace(name=self.zone, id='zone-id', compartment_id='compartment',
                               scope='GLOBAL', zone_type='SECONDARY', lifecycle_state='ACTIVE',
                               external_masters=[ExternalMaster(address=ip, tsig_key_id='key-1', port=53) for ip in ips],
                               nameservers=[SimpleNamespace(hostname='ns.example')])

    def args(self):
        return SimpleNamespace(old_ip='192.0.2.1', new_ip='192.0.2.2', lab_prefix='lab',
                               compartment_id='compartment', key_name_template='tsig.{zone}.')

    def test_stage_checks_require_exact_signed_ip_sets_and_lab_scope(self):
        validate_zone(self.zone_object(), self.args(), 'both')
        validate_zone(self.zone_object(('192.0.2.2',)), self.args(), 'b-only')
        for obj, mode in ((self.zone_object(), 'b-only'),
                          (self.zone_object(('192.0.2.2',)), 'both'),
                          (self.zone_object(('192.0.2.1', '192.0.2.2', '192.0.2.3')), 'both')):
            with self.assertRaises(ValueError):
                validate_zone(obj, self.args(), mode)
        obj = self.zone_object()
        obj.external_masters[1].tsig_key_id = 'other-key'
        with self.assertRaises(ValueError):
            validate_zone(obj, self.args(), 'both')
        obj.name = 'production.example.com'
        with self.assertRaises(ValueError):
            validate_zone(obj, self.args(), 'both')

    def test_remote_payload_compiles_without_shell_interpolation(self):
        helper = Path(__file__).resolve().parents[1].joinpath('scripts/oci_dns_transfer_remote.py').read_text()
        request = {'operation': 'notify', 'zones': [{'zone': self.zone}]}
        source = helper + '\ntry:\n    print(json.dumps(dispatch(json.loads(' + repr(json.dumps(request)) + "))))\nexcept Exception as exc:\n    print(json.dumps({'error': str(exc)}))\n    raise SystemExit(1)\n"
        compile(source, '<ssh-helper>', 'exec')


if __name__ == '__main__':
    unittest.main()
