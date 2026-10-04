import unittest

from iceman.diagnostics import (
    DockerDiagnosis,
    extract_application_signals,
)


class ApplicationSignalTests(unittest.TestCase):
    def test_repeated_down_signals_are_aggregated(self):
        logs = """
✅ AWS | 200 | 900 ms
❌ Openai | DOWN
❌ Broken Website | DOWN
❌ Openai | DOWN
❌ Broken Website | DOWN
❌ Openai | DOWN
"""
        signals = extract_application_signals(logs)
        by_subject = {signal.subject: signal for signal in signals}

        self.assertEqual(by_subject["Openai"].state, "DOWN")
        self.assertEqual(by_subject["Openai"].count, 3)
        self.assertEqual(by_subject["Broken Website"].count, 2)

    def test_success_lines_are_not_failure_signals(self):
        logs = """
✅ AWS | 200 | 900 ms
✅ Google | 200 | 1200 ms
"""
        self.assertEqual(extract_application_signals(logs), [])


if __name__ == "__main__":
    unittest.main()
