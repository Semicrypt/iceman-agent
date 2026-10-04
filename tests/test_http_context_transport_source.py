import unittest

from iceman.http_context import HttpContextSnapshot


class HttpContextTransportSourceTests(unittest.TestCase):
    def test_snapshot_can_record_live_transport_observation(self):
        snapshot = HttpContextSnapshot(
            context="application-container",
            client="axios",
            selected_remote_address="104.18.33.45",
            address_family="IPv4",
            tls_version="TLSv1.3",
            alpn_protocol="http/1.1",
            transport_observation_source=(
                "live https.request secureConnect event"
            ),
            transport_event="secureConnect",
        )
        self.assertIn(
            "live https.request",
            snapshot.transport_observation_source,
        )
        self.assertEqual(snapshot.transport_event, "secureConnect")


if __name__ == "__main__":
    unittest.main()
