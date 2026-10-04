import unittest

from iceman.safety import check_command


class SafetyTests(unittest.TestCase):
    def test_safe_read_command(self):
        self.assertTrue(check_command("docker ps -a").allowed)

    def test_terraform_destroy_blocked(self):
        result = check_command("terraform destroy")
        self.assertFalse(result.allowed)

    def test_sudo_blocked(self):
        self.assertFalse(check_command("sudo apt update").allowed)


if __name__ == "__main__":
    unittest.main()
