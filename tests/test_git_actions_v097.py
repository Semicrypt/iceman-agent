import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.git_actions import GitAction, GitRequest, build_git_plan
from iceman.runner import CommandResult


def git(cwd: Path, *args: str):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class GitActionsV097Tests(unittest.TestCase):
    def _repo(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        git(root, "init", "-b", "main")
        git(root, "config", "user.email", "test@example.com")
        git(root, "config", "user.name", "Iceman Test")
        return root

    def test_init_plan_for_non_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_git_plan(Path(tmp), GitRequest(action=GitAction.INIT))
        self.assertTrue(plan.safe_to_execute)
        self.assertEqual(plan.steps[0].command, "git init -b main")

    def test_stage_all_blocks_virtual_environment(self):
        root = self._repo()
        (root / ".venv").mkdir()
        (root / ".venv" / "junk.txt").write_text("x", encoding="utf-8")
        plan = build_git_plan(root, GitRequest(action=GitAction.STAGE_ALL))
        self.assertFalse(plan.safe_to_execute)
        self.assertTrue(any("Generated/heavy" in note for note in plan.notes))

    def test_stage_all_blocks_env_secret(self):
        root = self._repo()
        (root / ".env").write_text("API_KEY=secret", encoding="utf-8")
        plan = build_git_plan(root, GitRequest(action=GitAction.STAGE_ALL))
        self.assertFalse(plan.safe_to_execute)
        self.assertIn(".env", plan.sensitive_paths)

    def test_stage_all_allowed_for_safe_file(self):
        root = self._repo()
        (root / ".gitignore").write_text(".venv/\n.pytest_cache/\n", encoding="utf-8")
        (root / "README.md").write_text("hello", encoding="utf-8")
        plan = build_git_plan(root, GitRequest(action=GitAction.STAGE_ALL))
        self.assertTrue(plan.safe_to_execute)
        self.assertTrue(any(step.command == "git add -A" for step in plan.steps))

    def test_commit_requires_message(self):
        root = self._repo()
        (root / "README.md").write_text("hello", encoding="utf-8")
        git(root, "add", "README.md")
        plan = build_git_plan(root, GitRequest(action=GitAction.COMMIT))
        self.assertFalse(plan.safe_to_execute)
        self.assertTrue(any("commit message" in note.lower() for note in plan.notes))

    def test_push_requires_origin(self):
        root = self._repo()
        (root / "README.md").write_text("hello", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-m", "initial")
        plan = build_git_plan(root, GitRequest(action=GitAction.PUSH))
        self.assertFalse(plan.safe_to_execute)
        self.assertTrue(any("origin" in note for note in plan.notes))

    def test_push_tag_requires_origin(self):
        root = self._repo()
        (root / "README.md").write_text("hello", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-m", "initial")
        git(root, "tag", "-a", "v0.9.7", "-m", "v0.9.7")

        plan = build_git_plan(
            root,
            GitRequest(action=GitAction.PUSH_TAG, tag="v0.9.7"),
        )

        self.assertFalse(plan.safe_to_execute)
        self.assertTrue(any("origin" in note for note in plan.notes))

    def test_push_tag_builds_bounded_single_tag_plan(self):
        root = self._repo()
        (root / "README.md").write_text("hello", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-m", "initial")
        git(root, "tag", "-a", "v0.9.7", "-m", "v0.9.7")
        git(root, "remote", "add", "origin", "git@github.com:test/iceman-agent.git")

        plan = build_git_plan(
            root,
            GitRequest(action=GitAction.PUSH_TAG, tag="v0.9.7"),
        )

        self.assertTrue(plan.safe_to_execute)
        commands = [step.command for step in plan.steps]
        self.assertIn("git push origin v0.9.7", commands)
        self.assertTrue(
            any(
                "git ls-remote --exit-code --tags origin refs/tags/v0.9.7" == command
                for command in commands
            )
        )
        self.assertFalse(any("--force" in command for command in commands))

    def test_tag_refuses_to_replace_existing_tag(self):
        root = self._repo()
        (root / "README.md").write_text("hello", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-m", "initial")
        git(root, "tag", "-a", "v0.9.7", "-m", "v0.9.7")

        plan = build_git_plan(
            root,
            GitRequest(action=GitAction.TAG, tag="v0.9.7"),
        )

        self.assertFalse(plan.safe_to_execute)
        self.assertTrue(any("already exists" in note for note in plan.notes))

    def test_github_create_repo_defaults_private_and_does_not_push(self):
        root = self._repo()
        ok = CommandResult(command="gh auth status", returncode=0, stdout="ok", stderr="")
        with patch("iceman.git_actions.shutil.which", return_value="/usr/bin/gh"), patch(
            "iceman.git_actions._run", side_effect=lambda workspace, cmd: ok if cmd == "gh auth status" else (
                CommandResult(cmd, 0, "true\n", "") if cmd == "git rev-parse --is-inside-work-tree" else
                CommandResult(cmd, 0, "", "")
            )
        ):
            plan = build_git_plan(root, GitRequest(action=GitAction.GITHUB_CREATE_REPO, repo_name="iceman-agent"))
        self.assertTrue(plan.safe_to_execute)
        create = next(step.command for step in plan.steps if "gh repo create" in step.command)
        self.assertIn("--private", create)
        self.assertNotIn("--push", create)


if __name__ == "__main__":
    unittest.main()
