import unittest
from iceman.safety import check_command


class GitSafetyV097Tests(unittest.TestCase):
    def test_force_push_blocked(self):
        self.assertFalse(check_command("git push --force origin main").allowed)

    def test_hard_reset_blocked(self):
        self.assertFalse(check_command("git reset --hard HEAD~1").allowed)

    def test_git_clean_force_blocked(self):
        self.assertFalse(check_command("git clean -fd").allowed)

    def test_normal_push_allowed(self):
        self.assertTrue(check_command("git push -u origin main").allowed)


if __name__ == "__main__":
    unittest.main()
