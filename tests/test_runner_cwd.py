import tempfile
import unittest
from pathlib import Path

from iceman.runner import run_command


class RunnerCwdTests(unittest.TestCase):
    def test_command_runs_in_requested_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            result = run_command("pwd", cwd=target)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(Path(result.stdout.strip()), target)


if __name__ == "__main__":
    unittest.main()
