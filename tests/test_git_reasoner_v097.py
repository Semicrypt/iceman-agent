import tempfile
import unittest
from pathlib import Path

from iceman.git_actions import GitAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class GitReasonerV097Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = SessionState(Path(self.tmp.name))

    def test_initialize_git(self):
        decision = reason_request("initialize git", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_GIT_ACTION)
        self.assertEqual(decision.git_action, GitAction.INIT)

    def test_status(self):
        decision = reason_request("show git status", self.state)
        self.assertEqual(decision.git_action, GitAction.STATUS)

    def test_stage_all(self):
        decision = reason_request("stage everything", self.state)
        self.assertEqual(decision.git_action, GitAction.STAGE_ALL)

    def test_commit_message(self):
        decision = reason_request('commit my changes with message "feat: add git intelligence"', self.state)
        self.assertEqual(decision.git_action, GitAction.COMMIT)
        self.assertEqual(decision.commit_message, "feat: add git intelligence")

    def test_create_public_github_repo(self):
        decision = reason_request("create a public github repo called iceman-agent", self.state)
        self.assertEqual(decision.git_action, GitAction.GITHUB_CREATE_REPO)
        self.assertEqual(decision.repo_name, "iceman-agent")
        self.assertEqual(decision.visibility, "public")

    def test_push_to_github(self):
        decision = reason_request("push to github", self.state)
        self.assertEqual(decision.git_action, GitAction.PUSH)

    def test_git_typos_are_repaired(self):
        decision = reason_request("comit my chnages with message 'feat: test'", self.state)
        self.assertEqual(decision.git_action, GitAction.COMMIT)
        self.assertTrue(decision.requires_clarification)
        self.assertIn("commit", decision.interpreted_request)
        self.assertIn("changes", decision.interpreted_request)

    def test_push_named_tag_to_github(self):
        decision = reason_request("push tag v0.9.7 to github", self.state)
        self.assertEqual(decision.git_action, GitAction.PUSH_TAG)
        self.assertEqual(decision.tag, "v0.9.7")

    def test_push_the_named_tag(self):
        decision = reason_request("push the v0.9.7 tag", self.state)
        self.assertEqual(decision.git_action, GitAction.PUSH_TAG)
        self.assertEqual(decision.tag, "v0.9.7")

    def test_publish_named_tag(self):
        decision = reason_request("publish tag v0.9.7", self.state)
        self.assertEqual(decision.git_action, GitAction.PUSH_TAG)
        self.assertEqual(decision.tag, "v0.9.7")

    def test_tag(self):
        decision = reason_request("tag this as v0.9.7", self.state)
        self.assertEqual(decision.git_action, GitAction.TAG)
        self.assertEqual(decision.tag, "v0.9.7")


if __name__ == "__main__":
    unittest.main()
