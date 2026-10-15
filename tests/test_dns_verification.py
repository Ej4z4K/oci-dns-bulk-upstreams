import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_dns


class DnsVerificationTests(unittest.TestCase):
    def response(self, authoritative=True, truncated=False, code=0):
        request = dns.message.make_query('zone.test', 'SOA')
        reply = dns.message.make_response(request)
        reply.flags = dns.flags.AA if authoritative else 0
        if truncated:
            reply.flags |= dns.flags.TC
        reply.set_rcode(code)
        reply.answer.append(dns.rrset.from_text('zone.test.', 60, 'IN', 'SOA',
                                               'ns.zone.test. hostmaster.zone.test. 7 120 30 86400 60'))
        return reply

    def test_truncated_udp_retries_tcp(self):
        with patch('verify_dns.socket.getaddrinfo', return_value=[(2, 2, 17, '', ('192.0.2.1', 53))]), \
             patch('verify_dns.dns.query.udp', return_value=self.response(truncated=True)), \
             patch('verify_dns.dns.query.tcp', return_value=self.response()) as tcp:
            result = verify_dns.query('ns.example', 'zone.test', dns.rdatatype.SOA)
            self.assertEqual(result[0].serial, 7)
            tcp.assert_called_once()

    def test_non_authoritative_and_servfail_rejected(self):
        for reply in (self.response(authoritative=False), self.response(code=dns.rcode.SERVFAIL)):
            with patch('verify_dns.socket.getaddrinfo', return_value=[(2, 2, 17, '', ('192.0.2.1', 53))]), \
                 patch('verify_dns.dns.query.udp', return_value=reply):
                with self.assertRaises(ValueError):
                    verify_dns.query('ns.example', 'zone.test', dns.rdatatype.SOA)

    def test_serial_and_marker_must_both_match(self):
        soa = self.response().answer[0][0]
        txt = dns.rrset.from_text('probe.zone.test.', 60, 'IN', 'TXT', '"via-vm-b"')[0]
        for serial, marker, expected in ((7, 'via-vm-b', 'pass'), (8, 'via-vm-b', 'fail'),
                                         (7, 'old-marker', 'fail')):
            with patch('verify_dns.query', side_effect=[[soa], [txt]]):
                self.assertEqual(verify_dns.check('zone.test', 'ns.example', marker, serial)['status'], expected)
