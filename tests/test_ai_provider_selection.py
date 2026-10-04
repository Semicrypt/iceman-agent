import os
import unittest
from unittest.mock import patch

from iceman.ai.provider import provider_status


class ProviderSelectionTests(unittest.TestCase):
    def test_auto_without_key_uses_local(self):
        env = {
            "ICEMAN_AI_PROVIDER": "auto",
        }
        with patch.dict(os.environ, env, clear=True):
            status = provider_status()
        self.assertEqual(status.selected, "local")

    def test_auto_with_key_uses_openai(self):
        env = {
            "ICEMAN_AI_PROVIDER": "auto",
            "OPENAI_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=True):
            status = provider_status()
        self.assertEqual(status.selected, "openai")

    def test_explicit_openai_without_key_falls_back_local(self):
        env = {
            "ICEMAN_AI_PROVIDER": "openai",
        }
        with patch.dict(os.environ, env, clear=True):
            status = provider_status()
        self.assertEqual(status.selected, "local")


if __name__ == "__main__":
    unittest.main()
