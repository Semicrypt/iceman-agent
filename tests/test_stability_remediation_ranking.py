import tempfile
import unittest
from pathlib import Path

from iceman.application_remediation import build_application_remediation_advice
from iceman.probing import (
    EvidenceStability,
    HttpEvidenceSample,
    RootCauseInvestigation,
    SignalEmitter,
    TargetCandidate,
    TargetResolution,
    summarize_http_evidence,
)


def stability(context, statuses):
    return summarize_http_evidence(
        context,
        [
            HttpEvidenceSample(
                attempt=i + 1,
                status_code=status,
                accepted=200 <= status < 400,
                latency_ms=1,
                detail=f"HTTP {status}",
            )
            for i, status in enumerate(statuses)
        ],
    )


def investigation(root):
    candidate = TargetCandidate(
        subject="Openai",
        url="https://openai.com/",
        source_file="PostgreSQL services.url (matched by services.name)",
        line_number=None,
        score=100,
        context="",
        source_kind="database",
    )
    return RootCauseInvestigation(
        subject="Openai",
        workspace=root,
        resolution=TargetResolution(
            subject="Openai",
            resolved=True,
            selected=candidate,
        ),
        url="https://openai.com/",
        signal_emitter=SignalEmitter("backend", "be", 3),
    )


class StabilityRemediationRankingTests(unittest.TestCase):
    def test_host_mixed_source_stable_failure_prioritizes_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = investigation(Path(tmp))
            inv.session_host_stability = stability(
                "host",
                [403, 200, 403, 403, 403, 403],
            )
            inv.session_container_stability = stability(
                "container",
                [403, 403, 403, 403, 403, 403],
            )
            inv.session_source_stability = stability(
                "source",
                [403, 403, 403, 403, 403, 403],
            )

            advice = build_application_remediation_advice(inv)

        self.assertEqual(advice.options[0].action, "investigate_context")
        self.assertTrue(advice.options[0].recommended)
        self.assertIn("persistent application-context failure", advice.ranking_basis)

    def test_mixed_source_behavior_prioritizes_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = investigation(Path(tmp))
            inv.host_stability = stability("host", [403, 403, 403])
            inv.container_stability = stability("container", [403, 403, 403])
            inv.source_stability = stability("source", [403, 200, 403])

            advice = build_application_remediation_advice(inv)

        self.assertEqual(advice.options[0].action, "investigate_stability")
        self.assertTrue(advice.options[0].recommended)
        self.assertEqual(advice.options[2].action, "replace_target")


if __name__ == "__main__":
    unittest.main()
