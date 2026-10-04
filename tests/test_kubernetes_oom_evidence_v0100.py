from iceman.devops_actions import _k8s_oom_termination


def test_current_oomkilled_is_reported_as_current_termination():
    container = {
        "terminated_reason": "OOMKilled",
        "terminated_exit": "137",
        "last_reason": "",
        "last_exit": "",
    }

    assert _k8s_oom_termination(container) == ("current", "137")


def test_previous_oomkilled_is_reported_as_previous_termination():
    container = {
        "terminated_reason": "",
        "terminated_exit": "",
        "last_reason": "OOMKilled",
        "last_exit": "137",
    }

    assert _k8s_oom_termination(container) == ("previous", "137")
