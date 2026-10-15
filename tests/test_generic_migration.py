"""Portable input contracts and proof boundaries; these tests never contact OCI or DNS."""
import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import Mock, patch
import dns.zone
from oci.dns.models import ExternalMaster
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from oci_dns_config import load_options, sdk_config
from oci_dns_batch_common import desired_masters, run_batch
from verify_dns import marker_name
from oci_dns_transfer_evidence import completed_transfers
import oci_dns_verify_upstream as verifier
import oci_dns_export_zone_ids as exporter


class ConfigurationTests(unittest.TestCase):
    def test_cli_overrides_config_and_paths_follow_config_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'migration.json'
            path.write_text(json.dumps({'source_ip': '192.0.2.10', 'target_ip': '2001:db8::20',
                'zone_ids_file': 'ids.txt', 'output_dir': 'results', 'expected_count': 3,
                'verification': {'record_name': '_check', 'marker': 'fresh', 'logs_file': 'journal.jsonl'}}))
            args = Obj(config=path, old_ip='198.51.100.9', new_ip=None, zone_ids_file=None,
                       output_dir=None, expected_count=None, api_delay=None, record_name=None, marker=None, logs_file=None)
            load_options(args, {'api_delay': 0})
            self.assertEqual(args.old_ip, '198.51.100.9')
            self.assertEqual(args.new_ip, '2001:db8::20')
            self.assertEqual(args.zone_ids_file, Path(folder).resolve() / 'ids.txt')
            self.assertEqual(args.logs_file, Path(folder).resolve() / 'journal.jsonl')
            self.assertEqual(args.marker, 'fresh')

    def test_no_fixed_region_override(self):
        args = Obj(profile='TEAM', region=None)
        with patch('oci_dns_config.oci.config.from_file', return_value={'region': 'ap-sydney-1'}):
            self.assertEqual(sdk_config(args)['region'], 'ap-sydney-1')
            args.region = 'us-phoenix-1'
            self.assertEqual(sdk_config(args)['region'], 'us-phoenix-1')

    def test_config_cannot_enable_apply_or_contain_unknown_secret_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            for bad in ({'apply': True}, {'tsig_secret': 'do-not-store'}, {'expected_count': True},
                        {'expected_count': 0}, {'api_delay': -1}, {'verification': {'unknown': 1}}):
                path.write_text(json.dumps(bad))
                args = Obj(config=path, expected_count=None, api_delay=None)
                with self.assertRaises(ValueError):
                    load_options(args, {'api_delay': 0})

    def test_ipv6_plans_preserve_per_zone_keys_and_unrelated_masters(self):
        old = ExternalMaster(address='2001:0db8::10', tsig_key_id='zone-key')
        other = ExternalMaster(address='192.0.2.30', tsig_key_id='other-key', port=53)
        after, changed = desired_masters([old, other], '2001:db8::10', '2001:db8::20', 'add')
        self.assertTrue(changed)
        self.assertIs(after[1], other)
        self.assertEqual(after[2].tsig_key_id, 'zone-key')
        removed, changed = desired_masters(after, '2001:db8::10', '2001:db8::20', 'remove')
        self.assertEqual([m.address for m in removed], ['192.0.2.30', '2001:db8::20'])
        with self.assertRaises(ValueError):
            desired_masters([ExternalMaster(address='192.0.2.1', tsig_key_id='k', port=5353)],
                            '192.0.2.1', '192.0.2.2', 'add')

    def test_config_driven_ipv6_apply_uses_fresh_etag_and_zone_key(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'ids.txt').write_text('ocid1.dns-zone.oc1..example\n')
            path = folder / 'migration.json'
            path.write_text(json.dumps({'compartment_id': 'compartment', 'source_ip': '2001:db8::10',
                'target_ip': '2001:db8::20', 'zone_ids_file': 'ids.txt', 'output_dir': 'results',
                'expected_count': 1, 'api_delay': 0}))
            source = ExternalMaster(address='2001:0db8::10', tsig_key_id='per-zone-key')
            target = ExternalMaster(address='2001:db8::20', tsig_key_id='per-zone-key')
            def response(etag, masters):
                return Obj(headers={'etag': etag}, data=Obj(id='ocid1.dns-zone.oc1..example',
                    name='real.example.com', compartment_id='compartment', scope='GLOBAL',
                    zone_type='SECONDARY', lifecycle_state='ACTIVE', external_masters=masters))
            client = Mock()
            client.get_zone.side_effect = [response('before', [source]), response('fresh', [source]),
                                           response('after', [source, target])]
            with patch.object(sys, 'argv', ['batch', '--config', str(path), '--apply']), \
                 patch('oci_dns_batch_common.sdk_config', return_value={'region': 'ap-sydney-1'}), \
                 patch('oci_dns_batch_common.PacedDnsClient', return_value=client), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_batch('add'), 0)
            call = client.update_zone.call_args
            self.assertEqual(call.kwargs['if_match'], 'fresh')
            self.assertEqual(call.args[1].external_masters[1].address, '2001:db8::20')
            self.assertEqual(call.args[1].external_masters[1].tsig_key_id, 'per-zone-key')

    def test_generic_record_names_and_ipv6_log_clients(self):
        self.assertEqual(marker_name('real.example.com', '_check'), '_check.real.example.com.')
        self.assertEqual(marker_name('real.example.com', '_check.{zone}.'), '_check.real.example.com.')
        with self.assertRaises(ValueError):
            marker_name('real.example.com', 'outside.example.net.')
        line = "client @0x123 2001:db8::50#53/key company-key: transfer of 'real.example.com/IN': AXFR ended: 3 records (serial 77)"
        self.assertEqual(completed_transfers([line], 'real.example.com', 77, 'company-key.', {'2001:0db8::50'}), [line])


class GenericVerificationTests(unittest.TestCase):
    def run_case(self, mode='overlap', log_state=None, dns_pass=True, marker='fresh-value', ips=None):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            ids = folder / 'ids.txt'; ids.write_text('ocid1.dns-zone.oc1..example\n')
            ips = ips or (['2001:db8::10', '2001:db8::20'] if mode == 'overlap' else ['2001:db8::20'])
            zone = Obj(name='real.example.com', id='ocid1.dns-zone.oc1..example',
                compartment_id='compartment', scope='GLOBAL', zone_type='SECONDARY', lifecycle_state='ACTIVE',
                external_masters=[ExternalMaster(address=ip, tsig_key_id='key-id') for ip in ips],
                nameservers=[Obj(hostname='ns1.example'), Obj(hostname='ns2.example')])
            key = Obj(name='company-key.', algorithm='hmac-sha256', secret='NEVER_SERIALIZE_ME')
            argv = ['verify', '--compartment-id', 'compartment', '--zone-ids-file', str(ids),
                    '--source-ip', '2001:db8::10', '--target-ip', '2001:db8::20', '--expected-count', '1',
                    '--record-name', '_check', '--marker', 'fresh-value', '--api-delay', '0',
                    '--output-dir', str(folder / 'results')]
            if log_state is not None:
                log = folder / 'journal.jsonl'
                message = "client @0x123 2001:db8::50#54321/key company-key: transfer of 'real.example.com/IN': AXFR ended: 3 records (serial 77)"
                timestamp = '2000000' if log_state == 'fresh' else '100000'
                log.write_text(json.dumps({'MESSAGE': message, '__REALTIME_TIMESTAMP': timestamp}) + '\n')
                argv.extend(['--logs-file', str(log), '--transfer-since', '1970-01-01T00:00:01Z',
                             '--transfer-ips', '2001:db8::50'])
            transferred = dns.zone.from_text('''$ORIGIN real.example.com.
@ 60 IN SOA ns.real.example.com. hostmaster.real.example.com. 77 120 30 86400 60
@ 60 IN NS ns.real.example.com.
_check 60 IN TXT "''' + marker + '"\n')
            with patch.object(sys, 'argv', argv), patch.object(verifier, 'sdk_config', return_value={}), \
                 patch.object(verifier, 'PacedDnsClient') as factory, patch.object(verifier, 'signed_axfr', return_value=transferred) as axfr, \
                 patch.object(verifier, 'check', return_value={'status': 'pass' if dns_pass else 'fail'}), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                client = factory.return_value
                client.get_zone.return_value = Obj(data=zone)
                client.get_tsig_key.return_value = Obj(data=key)
                result = verifier.main(mode)
                client.update_zone.assert_not_called()
                reports = list((folder / 'results').glob('*.json'))
                report = json.loads(reports[0].read_text()) if reports else None
                if report:
                    self.assertNotIn(key.secret, json.dumps(report))
                return result, report, axfr.call_count

    def test_real_domain_and_ipv6_capability_pass_never_claims_oci_usage(self):
        code, report, _ = self.run_case()
        self.assertEqual((code, report['status']), (0, 'capability-and-data-pass'))
        self.assertIsNone(report['zones'][0]['ociTargetTransferObserved'])

    def test_fresh_log_evidence_proves_oci_target_transfer(self):
        code, report, _ = self.run_case(log_state='fresh')
        self.assertEqual((code, report['status']), (0, 'data-and-transfer-pass'))
        self.assertTrue(report['zones'][0]['ociTargetTransferObserved'])

    def test_old_logs_cannot_pass_transfer_proof(self):
        code, report, _ = self.run_case(log_state='old')
        self.assertEqual((code, report['status']), (2, 'target-transfer-not-observed'))

    def test_wrong_dns_or_marker_fails(self):
        for options in ({'dns_pass': False}, {'marker': 'old-value'}):
            code, report, _ = self.run_case(**options)
            self.assertEqual((code, report['status']), (1, 'fail'))

    def test_after_removal_requires_source_absent(self):
        self.assertEqual(self.run_case(mode='after-removal')[0], 0)
        code, report, calls = self.run_case(mode='after-removal', ips=['2001:db8::10', '2001:db8::20'])
        self.assertEqual((code, calls), (1, 0))

    def test_log_window_requires_timezone_and_structured_records(self):
        with self.assertRaises(ValueError):
            verifier.parse_since('2026-10-12T10:00:00')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'logs.txt'
            path.write_text(json.dumps({'MESSAGE': 'old un-timestamped evidence'}) + '\n')
            with self.assertRaises(ValueError):
                verifier.journal_messages(path, datetime.now(timezone.utc))


class ExportSelectionTests(unittest.TestCase):
    def invoke(self, missing=False):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            names = folder / 'names.txt'; names.write_text('example.com\nother.example.net\n')
            config = folder / 'migration.json'
            config.write_text(json.dumps({'compartment_id': 'compartment', 'name_prefix': 'ignored-prefix',
                'zone_ids_file': 'ids.txt', 'expected_count': 2, 'api_delay': 0}))
            zones = [Obj(id='ocid1.dns-zone.oc1..one', name='example.com')]
            if not missing:
                zones.append(Obj(id='ocid1.dns-zone.oc1..two', name='other.example.net'))
            argv = ['export', '--config', str(config), '--zone-names-file=' + str(names)]
            with patch.object(sys, 'argv', argv), patch.object(exporter, 'sdk_config', return_value={}), \
                 patch.object(exporter, 'PacedDnsClient') as factory, \
                 patch.object(exporter.oci.pagination, 'list_call_get_all_results', return_value=Obj(data=zones)), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                factory.return_value.get_zone.side_effect = [Obj(data=Obj(id=z.id, name=z.name,
                    lifecycle_state='ACTIVE', external_masters=[])) for z in zones]
                if missing:
                    with self.assertRaises(SystemExit):
                        exporter.main()
                    self.assertFalse((folder / 'ids.txt').exists())
                else:
                    exporter.main()
                    self.assertEqual(len((folder / 'ids.txt').read_text().splitlines()), 2)
                    factory.return_value.update_zone.assert_not_called()

    def test_exact_unrelated_names_override_config_prefix(self):
        self.invoke()

    def test_missing_name_never_writes_a_partial_selection(self):
        self.invoke(missing=True)
