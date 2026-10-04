import unittest
from pathlib import Path

from iceman.ai.contracts import AIIntent, TrustedAction
from iceman.ai.openai_provider import OpenAIReasoningProvider
from iceman.session import SessionState


class FakeResponse:
    def __init__(self, parsed):
        self.output_parsed = parsed


class FakeResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse(self.parsed)


class FakeClient:
    def __init__(self, parsed):
        self.responses = FakeResponses(parsed)


class OpenAIProviderTests(unittest.TestCase):
    def test_provider_returns_structured_intent(self):
        parsed = AIIntent(
            action=TrustedAction.ASSESS_PROJECT,
            target=None,
            rationale="Broad project health request.",
            confidence="high",
        )
        client = FakeClient(parsed)
        provider = OpenAIReasoningProvider(
            model="test-model",
            client=client,
        )

        result = provider.reason(
            "check the whole project",
            SessionState(Path(".")),
        )

        self.assertEqual(
            result.intent.action,
            TrustedAction.ASSESS_PROJECT,
        )
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "test-model")
        self.assertIs(
            client.responses.kwargs["text_format"],
            AIIntent,
        )


if __name__ == "__main__":
    unittest.main()
