import unittest

from iceman.http_context import _CONTAINER_CONTEXT_SCRIPT


class LiveTransportScriptTests(unittest.TestCase):
    def test_native_request_is_instrumented_before_axios_load(self):
        instrument_index = _CONTAINER_CONTEXT_SCRIPT.index(
            'instrumentRequest(https, "https")'
        )
        axios_index = _CONTAINER_CONTEXT_SCRIPT.index(
            'require("axios")'
        )
        self.assertLess(instrument_index, axios_index)

    def test_passive_socket_events_are_observed(self):
        self.assertIn('request.once("socket"', _CONTAINER_CONTEXT_SCRIPT)
        self.assertIn('socket.once("connect"', _CONTAINER_CONTEXT_SCRIPT)
        self.assertIn(
            'socket.once("secureConnect"',
            _CONTAINER_CONTEXT_SCRIPT,
        )

    def test_original_native_request_functions_are_restored(self):
        self.assertIn(
            "http.request = originalHttpRequest",
            _CONTAINER_CONTEXT_SCRIPT,
        )
        self.assertIn(
            "https.request = originalHttpsRequest",
            _CONTAINER_CONTEXT_SCRIPT,
        )

    def test_probe_does_not_construct_custom_agent(self):
        self.assertNotIn("new https.Agent", _CONTAINER_CONTEXT_SCRIPT)
        self.assertNotIn("rejectUnauthorized", _CONTAINER_CONTEXT_SCRIPT)


if __name__ == "__main__":
    unittest.main()
