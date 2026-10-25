"""Offline scope, proof-boundary and real MCP protocol tests. No OCI credentials."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from mcp import Client, StdioServerParameters
import oci

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from oci_dns_mcp import ReadError, ReadOnlyDns, Settings, build_server, read_retry_strategy

COMPARTMENT = "ocid1.compartment.oc1..example"
ZONE_ID = "ocid1.dns-zone.oc1..example"
SECRET = "DO-NOT-EXPOSE-THIS-SECRET"


def zone(**changes):
    fields = dict(id=ZONE_ID, name="example.test", compartment_id=COMPARTMENT,
                  zone_type="SECONDARY", scope="GLOBAL", lifecycle_state="ACTIVE",
                  external_masters=[NS(address="192.0.2.10", port=53,
                                       tsig_key_id="ocid1.dns-tsig-key.oc1..example", secret=SECRET)],
                  nameservers=[NS(hostname="ns1.example.test"), NS(hostname="ns2.example.test")],
                  secret=SECRET)
    fields.update(changes)
    return NS(**fields)


def setup_service(dns_check=None):
    # No write/key methods exist on this mock: accidental use fails immediately.
    backend = Mock(spec_set=["list_zones", "get_zone"])
    backend.get_zone.return_value = NS(data=zone())
    backend.list_zones.return_value = NS(data=[NS(id=ZONE_ID)], headers={"opc-next-page": "next-token"})
    query = dns_check or Mock(return_value={"status": "pass", "soaSerial": 7, "txt": ["fresh"]})
    factory = Mock(return_value=backend)
    settings = Settings(COMPARTMENT, api_delay=0)
    return ReadOnlyDns(settings, factory, query), backend, factory, query


class ScopeAndVerificationTests(unittest.TestCase):
    def test_listing_is_paged_and_returns_current_upstreams_without_secrets(self):
        service, backend, factory, _ = setup_service()
        result = service.list_zones(2, "previous-token")
        backend.list_zones.assert_called_once_with(COMPARTMENT, zone_type="SECONDARY",
                                                  scope="GLOBAL", limit=2, page="previous-token")
        backend.get_zone.assert_called_once_with(ZONE_ID, scope="GLOBAL")
        self.assertEqual(result["next_page"], "next-token")
        self.assertEqual(result["zones"][0]["upstreams"][0]["address"], "192.0.2.10")
        self.assertIn("tsigKeyId", result["zones"][0]["upstreams"][0])
        self.assertNotIn(SECRET, json.dumps(result))
        factory.assert_called_once()

    def test_scope_rejects_other_compartment_primary_and_private(self):
        for changes in ({"compartment_id": "another"}, {"zone_type": "PRIMARY"}, {"scope": "PRIVATE"}):
            with self.subTest(changes=changes):
                service, backend, _, _ = setup_service()
                backend.get_zone.return_value = NS(data=zone(**changes))
                with self.assertRaises(ReadError):
                    service.get_zone(ZONE_ID)
                with self.assertRaises(ReadError):
                    service.list_zones()

    def test_invalid_zone_id_is_rejected_before_loading_credentials(self):
        service, _, factory, _ = setup_service()
        with self.assertRaises(ReadError):
            service.get_zone("https://example.test/not-a-zone")
        factory.assert_not_called()

    def test_page_bounds_fail_before_api_calls(self):
        service, _, factory, _ = setup_service()
        for limit in (0, 51, True, "20"):
            with self.assertRaises(ReadError):
                service.list_zones(limit)
        with self.assertRaises(ReadError):
            service.list_zones(page="x" * 4097)
        factory.assert_not_called()

    def test_oversized_api_page_does_not_trigger_unbounded_detail_reads(self):
        service, backend, _, _ = setup_service()
        backend.list_zones.return_value.data = [NS(id=ZONE_ID)] * 3
        with self.assertRaises(ReadError):
            service.list_zones(2)
        backend.get_zone.assert_not_called()

    def test_empty_page_retains_continuation_token(self):
        service, backend, _, _ = setup_service()
        backend.list_zones.return_value.data = []
        self.assertEqual(service.list_zones()["next_page"], "next-token")
        backend.get_zone.assert_not_called()

    def test_verification_calls_all_assigned_servers_without_claiming_transfer_proof(self):
        service, _, _, query = setup_service()
        result = service.verify_zone(ZONE_ID, 7, "fresh")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["proof_level"], "authoritative-soa-txt")
        self.assertIsNone(result["oci_target_transfer_observed"])
        self.assertEqual({c.args[1] for c in query.call_args_list}, {"ns1.example.test", "ns2.example.test"})
        for call in query.call_args_list:
            self.assertEqual(call.args[2:], ("fresh", 7, "_oci-transfer-check"))

    def test_one_failed_nameserver_fails_verification(self):
        service, _, _, _ = setup_service(Mock(side_effect=[{"status": "pass"}, {"status": "fail"}]))
        self.assertEqual(service.verify_zone(ZONE_ID, 7, "fresh")["status"], "fail")

    def test_concurrent_upstream_change_fails_verification(self):
        service, backend, _, _ = setup_service()
        changed = zone(external_masters=[NS(address="192.0.2.20", port=53, tsig_key_id="other")])
        backend.get_zone.side_effect = [NS(data=zone()), NS(data=changed)]
        result = service.verify_zone(ZONE_ID, 7, "fresh")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(result["configuration_unchanged"])

    def test_outside_marker_inactive_and_missing_nameservers_never_query_dns(self):
        for changes, owner in (({}, "outside.example."), ({"lifecycle_state": "UPDATING"}, "probe"),
                               ({"nameservers": []}, "probe")):
            service, backend, _, query = setup_service()
            backend.get_zone.return_value = NS(data=zone(**changes))
            with self.assertRaises(ReadError):
                service.verify_zone(ZONE_ID, 7, "fresh", owner)
            query.assert_not_called()

    def test_bad_verification_inputs_do_not_load_credentials(self):
        service, _, factory, _ = setup_service()
        for serial, marker in ((True, "fresh"), (-1, "fresh"), (4294967296, "fresh"), (7, "")):
            with self.assertRaises(ReadError):
                service.verify_zone(ZONE_ID, serial, marker)
        factory.assert_not_called()


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_annotations_schemas_and_structured_results(self):
        service, backend, _, _ = setup_service()
        async with Client(build_server(Settings(COMPARTMENT), service)) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual({t.name for t in tools}, {"list_secondary_zones", "get_secondary_zone", "verify_secondary_zone"})
            for tool in tools:
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertNotIn("profile", tool.input_schema["properties"])
                self.assertNotIn("compartment_id", tool.input_schema["properties"])
            result = await client.call_tool("get_secondary_zone", {"zone_id": ZONE_ID})
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["zone_id"], ZONE_ID)
            self.assertNotIn(SECRET, json.dumps(result.structured_content))
            bad = await client.call_tool("list_secondary_zones", {"limit": 999})
            self.assertTrue(bad.is_error)
            backend.list_zones.assert_not_called()
            unknown = await client.call_tool("update_zone", {"zone_id": ZONE_ID})
            self.assertTrue(unknown.is_error)

    async def test_protocol_verification_returns_freshness_proof_only(self):
        service, _, _, _ = setup_service()
        async with Client(build_server(Settings(COMPARTMENT), service)) as client:
            result = await client.call_tool("verify_secondary_zone", {
                "zone_id": ZONE_ID, "expected_serial": 7, "marker": "fresh"})
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["status"], "pass")
            self.assertIsNone(result.structured_content["oci_target_transfer_observed"])

    async def test_tools_reject_scope_and_execution_override_arguments(self):
        service, backend, _, _ = setup_service()
        async with Client(build_server(Settings(COMPARTMENT), service)) as client:
            for name in ("compartment_id", "profile", "region", "apply", "command", "path", "api_delay"):
                result = await client.call_tool("get_secondary_zone", {"zone_id": ZONE_ID, name: "override"})
                self.assertTrue(result.is_error, name)
            backend.get_zone.assert_not_called()

    async def test_config_and_oci_error_details_are_not_exposed(self):
        for error in (ValueError(SECRET), oci.exceptions.ServiceError(401, "NotAuthenticated", {}, SECRET)):
            service, _, factory, _ = setup_service()
            factory.side_effect = error
            async with Client(build_server(Settings(COMPARTMENT), service)) as client:
                result = await client.call_tool("get_secondary_zone", {"zone_id": ZONE_ID})
                self.assertTrue(result.is_error)
                self.assertNotIn(SECRET, str(result.content))

    async def test_429_is_reported_as_throttling_without_permission_advice(self):
        service, backend, _, _ = setup_service()
        backend.get_zone.side_effect = oci.exceptions.ServiceError(429, "TooManyRequests", {}, SECRET)
        async with Client(build_server(Settings(COMPARTMENT), service)) as client:
            result = await client.call_tool("get_secondary_zone", {"zone_id": ZONE_ID})
            self.assertTrue(result.is_error)
            text = str(result.content)
            self.assertIn("rate limiting", text)
            self.assertIn("--api-delay", text)
            self.assertNotIn(SECRET, text)
            self.assertNotIn("permissions", text)

    async def test_actual_stdio_handshake_needs_no_oci_credentials(self):
        async def exercise():
            params = StdioServerParameters(command=sys.executable, args=[
                str(ROOT / "scripts/oci_dns_mcp.py"), "--compartment-id", COMPARTMENT,
                "--config-file", str(ROOT / "does-not-exist-config")])
            async with Client(params) as client:
                self.assertEqual(len((await client.list_tools()).tools), 3)
                result = await client.call_tool("get_secondary_zone", {"zone_id": "invalid"})
                self.assertTrue(result.is_error)
                self.assertIn("Supply a DNS zone OCID", str(result.content))
        await asyncio.wait_for(exercise(), timeout=20)


class ThrottlingTests(unittest.TestCase):
    @staticmethod
    def error(status):
        return oci.exceptions.ServiceError(status, "TooManyRequests" if status == 429 else "Other", {}, SECRET)

    def test_all_reads_share_pacing(self):
        _, backend, factory, _ = setup_service()
        now, waits = [0.0], []
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        service = ReadOnlyDns(Settings(COMPARTMENT), factory, clock=lambda: now[0], sleep=sleep)
        service.list_zones(1)
        service.get_zone(ZONE_ID)
        self.assertEqual(waits, [1.0, 1.0])
        self.assertEqual(backend.get_zone.call_count, 2)

    def test_failed_read_is_also_paced(self):
        _, backend, factory, _ = setup_service()
        backend.get_zone.side_effect = [self.error(401), NS(data=zone())]
        now, waits = [0.0], []
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        service = ReadOnlyDns(Settings(COMPARTMENT, api_delay=2), factory,
                              clock=lambda: now[0], sleep=sleep)
        with self.assertRaises(oci.exceptions.ServiceError):
            service.get_zone(ZONE_ID)
        service.get_zone(ZONE_ID)
        self.assertEqual(waits, [2.0])

    def test_invalid_delay_is_rejected(self):
        for value in (-1, 61, True, float('nan'), float('inf')):
            with self.assertRaises(ReadError):
                Settings(COMPARTMENT, api_delay=value)

    def test_429_recovers_with_bounded_exponential_backoff(self):
        operation = Mock(__name__="read", side_effect=[self.error(429), self.error(429), "ok"])
        with patch('oci.retry.retry.time.sleep') as sleep:
            self.assertEqual(read_retry_strategy().make_retrying_call(operation), "ok")
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(0 < call.args[0] <= 8 for call in sleep.call_args_list))

    def test_permanent_429_stops_after_four_attempts(self):
        operation = Mock(__name__="read", side_effect=self.error(429))
        with patch('oci.retry.retry.time.sleep') as sleep:
            with self.assertRaises(oci.exceptions.ServiceError):
                read_retry_strategy().make_retrying_call(operation)
        self.assertEqual(operation.call_count, 4)
        self.assertEqual(sleep.call_count, 3)

    def test_auth_and_not_found_are_not_retried_but_503_is(self):
        for status in (400, 401, 403, 404):
            operation = Mock(__name__="read", side_effect=self.error(status))
            with patch('oci.retry.retry.time.sleep') as sleep:
                with self.assertRaises(oci.exceptions.ServiceError):
                    read_retry_strategy().make_retrying_call(operation)
            self.assertEqual(operation.call_count, 1)
            sleep.assert_not_called()
        operation = Mock(__name__="read", side_effect=[self.error(503), "ok"])
        with patch('oci.retry.retry.time.sleep'):
            self.assertEqual(read_retry_strategy().make_retrying_call(operation), "ok")

    def test_actual_sdk_listing_recovers_from_429_and_returns_inventory(self):
        client = oci.dns.DnsClient({'region': 'uk-london-1'},
                                   signer=Mock(spec=oci.auth.signers.SecurityTokenSigner),
                                   retry_strategy=read_retry_strategy())
        client.base_client.call_api = Mock(__name__="call_api", side_effect=[self.error(429),
            NS(data=[NS(id=ZONE_ID)], headers={}), NS(data=zone())])
        service = ReadOnlyDns(Settings(COMPARTMENT, api_delay=0), lambda _: client)
        with patch('oci.retry.retry.time.sleep'):
            result = service.list_zones(1)
        self.assertEqual(result['zones'][0]['zone_id'], ZONE_ID)
        self.assertEqual(client.base_client.call_api.call_count, 3)


if __name__ == "__main__":
    unittest.main()
