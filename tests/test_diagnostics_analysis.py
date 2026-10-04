import unittest

from iceman.diagnostics import DockerDiagnosis, _analyze_logs


class DiagnosticAnalysisTests(unittest.TestCase):
    def test_econnrefused_detection(self):
        diagnosis = DockerDiagnosis(target="demo")
        _analyze_logs("Error: connect ECONNREFUSED 127.0.0.1:5432", diagnosis)
        titles = {item.title for item in diagnosis.findings}
        self.assertIn("Application connection refused", titles)

    def test_nginx_emerg_detection(self):
        diagnosis = DockerDiagnosis(target="demo")
        _analyze_logs("nginx: [emerg] invalid number of arguments", diagnosis)
        titles = {item.title for item in diagnosis.findings}
        self.assertIn("Nginx startup failure", titles)


if __name__ == "__main__":
    unittest.main()
