import unittest

from iceman.probing import normalize_investigation_subject


class InvestigationSubjectNormalizationTests(unittest.TestCase):
    def test_plain_subject(self):
        self.assertEqual(
            normalize_investigation_subject("Openai"),
            "Openai",
        )

    def test_investigate_why_subject_is_down(self):
        self.assertEqual(
            normalize_investigation_subject(
                "investigate why Openai is down"
            ),
            "Openai",
        )

    def test_why_is_subject_down(self):
        self.assertEqual(
            normalize_investigation_subject(
                "why is Openai down?"
            ),
            "Openai",
        )

    def test_check_why_subject_is_failing(self):
        self.assertEqual(
            normalize_investigation_subject(
                "check why Broken Website is failing"
            ),
            "Broken Website",
        )

    def test_subject_is_offline(self):
        self.assertEqual(
            normalize_investigation_subject(
                "Broken Website is offline"
            ),
            "Broken Website",
        )


if __name__ == "__main__":
    unittest.main()
