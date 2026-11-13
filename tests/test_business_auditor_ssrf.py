from collections.abc import Iterable
import socket
import unittest
from unittest.mock import patch

import httpcore
import httpx

import business_auditor


class PublicHostValidationTests(unittest.TestCase):
    @patch.object(business_auditor.socket, "getaddrinfo", side_effect=socket.gaierror("DNS failed"))
    def test_dns_failure_is_rejected(self, _getaddrinfo):
        self.assertFalse(business_auditor.host_is_public("business.example"))

    @patch.object(business_auditor.socket, "getaddrinfo", return_value=[])
    def test_empty_dns_result_is_rejected(self, _getaddrinfo):
        self.assertFalse(business_auditor.host_is_public("business.example"))

    @patch.object(
        business_auditor.socket,
        "getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0)),
        ],
    )
    def test_mixed_public_and_private_dns_results_are_rejected(self, _getaddrinfo):
        self.assertFalse(business_auditor.host_is_public("business.example"))

    @patch.object(business_auditor.socket, "getaddrinfo", side_effect=socket.gaierror("DNS failed"))
    def test_request_guard_rejects_unresolvable_host(self, _getaddrinfo):
        request = httpx.Request("GET", "https://business.example/")

        with self.assertRaises(httpx.RequestError):
            business_auditor.guard_request(request)

    @patch.object(
        business_auditor.socket,
        "getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )
    def test_network_backend_connects_to_validated_ip(self, _getaddrinfo):
        connected_hosts: list[str] = []

        def fake_connect(
            backend: httpcore.SyncBackend,
            host: str,
            port: int,
            timeout: float | None,
            local_address: str | None,
            socket_options: Iterable[httpcore.SOCKET_OPTION] | None,
        ) -> object:
            self.assertIsInstance(backend, httpcore.SyncBackend)
            self.assertEqual(port, 443)
            self.assertIsNone(timeout)
            self.assertIsNone(local_address)
            self.assertIsNone(socket_options)
            connected_hosts.append(host)
            return object()

        with patch.object(httpcore.SyncBackend, "connect_tcp", autospec=True, side_effect=fake_connect):
            backend = business_auditor.PublicNetworkBackend()
            backend.connect_tcp("business.example", 443)

        self.assertEqual(connected_hosts, ["8.8.8.8"])

    @patch.object(
        business_auditor.socket,
        "getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0))],
    )
    def test_network_backend_never_connects_to_private_address(self, _getaddrinfo):
        with patch.object(httpcore.SyncBackend, "connect_tcp", autospec=True) as connect:
            backend = business_auditor.PublicNetworkBackend()

            with self.assertRaises(httpcore.ConnectError):
                backend.connect_tcp("business.example", 443)

        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
