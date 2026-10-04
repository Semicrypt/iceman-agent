import unittest

from iceman.shell import _legacy_docker_diagnose_target


class ShellDiagnoseRoutingV0100Tests(unittest.TestCase):
    def test_plain_diagnose_remains_legacy_docker(self):
        self.assertEqual(
            _legacy_docker_diagnose_target("diagnose broken-backend"),
            "broken-backend",
        )

    def test_explicit_pod_diagnosis_falls_through(self):
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose pod iceman-crashloop-v0100"
            )
        )

    def test_explicit_kubernetes_pod_diagnosis_falls_through(self):
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose kubernetes pod api-abc"
            )
        )

    def test_explicit_k8s_pod_diagnosis_falls_through(self):
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose k8s pod api-abc"
            )
        )

    def test_the_pod_form_falls_through(self):
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose the pod api-abc"
            )
        )


if __name__ == "__main__":
    unittest.main()
