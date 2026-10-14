import pytest

from iceman.ai.contracts import TrustedAction
from iceman.ai.local_provider import LocalReasoningProvider
from iceman.linux_actions import LinuxAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


@pytest.mark.parametrize("prompt", [
    "list my folders",
    "list folders",
    "list my files",
    "show files here",
    "show me the files",
    "list the files",
    "show my folders",
    "show folders here",
    "list directories",
    "list my directories",
    "list files and folders",
    "show folders and files here",
    "list files in this folder",
    "show files in the current directory",
    "list folders in my current working directory",
    "what files are here?",
    "  LIST   MY  FOLDERS!  ",
    "please list my folders",
])
def test_current_folder_listing_uses_trusted_linux_action(tmp_path, prompt):
    state = SessionState(tmp_path)
    decision = reason_request(prompt, state)
    assert decision.action is ReasoningAction.EXECUTE_LINUX_ACTION
    assert decision.linux_action is LinuxAction.LIST
    assert decision.path is None
    assert not decision.requires_clarification

    intent = LocalReasoningProvider().reason(prompt, state).intent
    assert intent.action is TrustedAction.EXECUTE_LINUX_ACTION
    assert intent.linux_action is LinuxAction.LIST
    assert intent.path is None
    assert not intent.requires_clarification


@pytest.mark.parametrize("prompt", [
    "analyze my current folder and files",
    "analyze this folder",
    "inspect current folder",
    "analyze current directory",
    "inspect this directory",
    "inspect my current folder",
    "inspect the current directory",
    "analyze my folder",
    "analyse this folder",
    "analyze the current working directory",
    "analyze this project",
    "inspect current workspace",
    "inspect this folder and its files",
    "  ANALYZE   THIS  FOLDER!  ",
    "please inspect current folder",
])
def test_folder_analysis_uses_existing_read_only_inspection(tmp_path, prompt):
    state = SessionState(tmp_path)
    decision = reason_request(prompt, state)
    assert decision.action is ReasoningAction.INSPECT
    assert decision.linux_action is None
    assert decision.devops_action is None
    assert decision.path is None
    assert not decision.requires_clarification

    intent = LocalReasoningProvider().reason(prompt, state).intent
    assert intent.action is TrustedAction.INSPECT_PROJECT
    assert intent.path is None
    assert not intent.requires_clarification


@pytest.mark.parametrize("prompt", [
    "list folders in Works",
    "list my files in /tmp",
    "list folders and delete them",
    "list files; touch unexpected",
    "show folders here && touch unexpected",
    "analyze this folder and delete files",
    "analyze this folder in Works",
    "inspect current folder /tmp",
    "inspect current folder; touch unexpected",
    "analyze deployment plan",
])
def test_folder_shortcuts_do_not_capture_other_targets_or_compound_requests(tmp_path, prompt):
    decision = reason_request(prompt, SessionState(tmp_path))
    assert decision.action is not ReasoningAction.INSPECT
    assert decision.linux_action is not LinuxAction.LIST


@pytest.mark.parametrize("prompt", ["show hidden files", "list all files", "list hidden files"])
def test_hidden_listing_keeps_its_existing_action(tmp_path, prompt):
    decision = reason_request(prompt, SessionState(tmp_path))
    assert decision.linux_action is LinuxAction.LIST_ALL


def test_inspection_typo_correction_still_requires_clarification(tmp_path):
    intent = LocalReasoningProvider().reason(
        "inspect current foler", SessionState(tmp_path),
    ).intent
    assert intent.action is TrustedAction.INSPECT_PROJECT
    assert intent.requires_clarification
    assert intent.interpreted_request == "inspect current folder"


@pytest.mark.parametrize("prompt,action", [
    ("list my folders", TrustedAction.EXECUTE_LINUX_ACTION),
    ("show files here", TrustedAction.EXECUTE_LINUX_ACTION),
    ("analyze my current folder and files", TrustedAction.INSPECT_PROJECT),
    ("inspect current folder", TrustedAction.INSPECT_PROJECT),
])
def test_current_folder_words_are_not_reinterpreted_as_named_resources(tmp_path, prompt, action):
    for name in ("my-folders", "current-folder", "files-here"):
        (tmp_path / name).mkdir()
    intent = LocalReasoningProvider().reason(prompt, SessionState(tmp_path)).intent
    assert intent.action is action
    assert intent.path is None
    assert not intent.requires_clarification
