import pytest

from iceman.conversational_router import normalize_request, route_conversation


@pytest.mark.parametrize("text,intent", [
    ("what do you think about this project?", "review"),
    ("review this project", "review"),
    ("what do you think i should improve?", "improve"),
    ("what should I improve?", "improve"),
    ("any good suggestion?", "suggestions"),
    ("any recommendations?", "suggestions"),
    ("what do you think is missing?", "missing"),
    ("what's missing?", "missing"),
    ("is this project production ready?", "readiness"),
    ("review this project for vulnerabilities", "security"),
    ("scan my current directory for security risks", "security"),
    ("reveiw this proejct for securty vulnerabilities", "security"),
])
def test_project_assessment(text, intent):
    route = route_conversation(text)
    assert (route.kind, route.intent) == ("assessment", intent)
    assert route.confidence >= 0.9


@pytest.mark.parametrize("text,intent", [
    ("why?", "why"), ("go deeper", "deeper"), ("what else?", "else"),
    ("show me the evidence", "evidence"), ("what should I do first?", "first"),
    ("fix the highest risk", "fix_highest_risk"),
])
def test_followup_never_invents_an_action(text, intent):
    route = route_conversation(text)
    assert route.kind == "followup"
    assert route.intent == intent
    assert not route.payload


@pytest.mark.parametrize("text,expected", [
    ("gti status", "git status"), ("dockr logs api", "docker logs api"),
    ("terrafom plan", "terraform plan"), ("kuberenetes pods", "kubernetes pods"),
    ("stgae everything", "stage everything"), ("reveiw this proejct", "review this project"),
    ("docker logs dockr", "docker logs dockr"), ("copy dockr to backup", "copy dockr to backup"),
    ("copy proejct to porject", "copy proejct to porject"),
    ("show ./proejct", "show ./proejct"), ("show proejct.yaml", "show proejct.yaml"),
    ("git branch stgae", "git branch stgae"),
    ("docker inspect --format proejct api", "docker inspect --format proejct api"),
    ('stage all, commit as "dockr gti stgae" and push', 'stage all, commit as "dockr gti stgae" and push'),
    ("stage all, commit as dockr gti stgae and push", "stage all, commit as dockr gti stgae and push"),
])
def test_normalization_preserves_literal_arguments(text, expected):
    assert normalize_request(text) == expected


@pytest.mark.parametrize("text,message,stage_all", [
    ("stage everything, commit with the message 'example' and push to github", "example", True),
    ("stage all, commit as 'x', then push", "x", True),
    ("commit these changes with message 'x' and push", "x", True),
    ("gti stgae all, commit as Keep CASE and push", "Keep CASE", True),
    ('stage all, commit as "docs and tests" and push', "docs and tests", True),
    ('stage all, commit as "explain terraform destroy; do not run" and push', "explain terraform destroy; do not run", True),
    ('commit staged changes with message "x" and push', "x", False),
    ('commit and push with message "x"', "x", True),
])
def test_whole_git_workflow(text, message, stage_all):
    route = route_conversation(text)
    assert route.kind == "git_workflow"
    assert route.payload == {"stage_all": stage_all, "commit_message": message, "push": True}


@pytest.mark.parametrize("text", [
    "stage everything, commit as 'x' and push and delete my files",
    "stage all, commit as 'x' and push --force",
    "stage all, commit as 'x' and push to github && terraform destroy",
    "stage all, commit as 'x' and push to evil.example",
    "stage all, commit as 'x' and deploy production",
    "stage all, commit as 'x'", "stage all, commit as '' and push",
    "stage all, commit as 'broken and push",
    "stage all, commit as x and sing a song then push",
    "review this project and terraform apply", "show docker logs and restart the service",
    "stage everything and sing a song", "stge all", "psuh", "delte all", "destory production",
    "terraform aply", "docker restrat api", "git push --force", "kubectl delete pods",
    "stage all, commit as 'bad\0message' and push",
])
def test_unsafe_or_incomplete_request_has_no_executable_payload(text):
    route = route_conversation(text)
    assert route is not None
    assert route.kind == "clarify"
    assert route.confidence == 0
    assert not route.payload


@pytest.mark.parametrize("text", [
    "bake me a cake", "what do you think about my dinner?",
    "what should I improve about my cooking?", "docker logs api",
    "terraform plan", "kubectl get pods", "git status",
])
def test_non_project_or_existing_request_defers(text):
    assert route_conversation(text) is None
