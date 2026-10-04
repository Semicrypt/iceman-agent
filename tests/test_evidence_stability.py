import unittest

from iceman.probing import HttpEvidenceSample, summarize_http_evidence


def sample(attempt, status, accepted):
    return HttpEvidenceSample(
        attempt=attempt,
        status_code=status,
        accepted=accepted,
        latency_ms=10,
        detail=str(status),
    )


class EvidenceStabilityTests(unittest.TestCase):
    def test_stable_failure(self):
        stability = summarize_http_evidence(
            "backend",
            [
                sample(1, 403, False),
                sample(2, 403, False),
                sample(3, 403, False),
            ],
        )
        self.assertEqual(stability.classification, "stable_failure")
        self.assertEqual(stability.failure_count, 3)
        self.assertEqual(stability.status_counts, {403: 3})

    def test_unstable_mixed(self):
        stability = summarize_http_evidence(
            "host",
            [
                sample(1, 403, False),
                sample(2, 200, True),
                sample(3, 403, False),
            ],
        )
        self.assertEqual(stability.classification, "unstable_mixed")
        self.assertEqual(stability.success_count, 1)
        self.assertEqual(stability.failure_count, 2)

    def test_stable_success(self):
        stability = summarize_http_evidence(
            "host",
            [
                sample(1, 200, True),
                sample(2, 200, True),
                sample(3, 204, True),
            ],
        )
        self.assertEqual(stability.classification, "stable_success")


if __name__ == "__main__":
    unittest.main()
