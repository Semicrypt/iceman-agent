import tempfile
import unittest
from pathlib import Path

from iceman.probing import (
    HttpEvidenceSample,
    RootCauseInvestigation,
    TargetResolution,
)
from iceman.session import SessionState


def samples(statuses):
    return [
        HttpEvidenceSample(
            attempt=index + 1,
            status_code=status,
            accepted=200 <= status < 400,
            latency_ms=1,
            detail=f"HTTP {status}",
        )
        for index, status in enumerate(statuses)
    ]


class SessionEvidenceHistoryTests(unittest.TestCase):
    def test_repeated_investigations_accumulate_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = SessionState(root)

            first = RootCauseInvestigation(
                subject="Openai",
                workspace=root,
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
                url="https://openai.com/",
                host_http_samples=samples([403, 403, 403]),
            )
            state.remember_investigation(first)

            second = RootCauseInvestigation(
                subject="Openai",
                workspace=root,
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
                url="https://openai.com/",
                host_http_samples=samples([200, 403, 403]),
            )
            state.remember_investigation(second)

            self.assertEqual(
                len(second.session_host_stability.samples),
                6,
            )
            self.assertEqual(
                second.session_host_stability.classification,
                "unstable_mixed",
            )

    def test_url_change_does_not_mix_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = SessionState(root)

            first = RootCauseInvestigation(
                subject="Openai",
                workspace=root,
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
                url="https://old.example/",
                host_http_samples=samples([403, 403, 403]),
            )
            state.remember_investigation(first)

            second = RootCauseInvestigation(
                subject="Openai",
                workspace=root,
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
                url="https://new.example/",
                host_http_samples=samples([200, 200, 200]),
            )
            state.remember_investigation(second)

            self.assertEqual(
                len(second.session_host_stability.samples),
                3,
            )
            self.assertEqual(
                second.session_host_stability.classification,
                "stable_success",
            )


if __name__ == "__main__":
    unittest.main()
