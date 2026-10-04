import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.diagnostics import DockerDiagnosis, classify_runtime_error
from iceman.remediation import build_remediation_plan, resolve_compose_workdir


SOCKET_ERROR = (
    'failed to create task for container: OCI runtime create failed: '
    'error mounting "/some/docker.sock" to rootfs at "/var/run/docker.sock": '
    'not a directory'
)


class RuntimePriorityTests(unittest.TestCase):
    def test_classifies_docker_socket_mount(self):
        self.assertEqual(
            classify_runtime_error(SOCKET_ERROR),
            "docker_socket_bind_mount",
        )

    def test_compose_workdir_prefers_container_label(self):
        diagnosis = DockerDiagnosis(
            target="demo",
            compose_working_dir="/tmp",
        )
        self.assertEqual(
            resolve_compose_workdir(diagnosis, Path("/")),
            Path("/tmp"),
        )

    def test_runtime_socket_error_recreates_without_rebuild(self):
        diagnosis = DockerDiagnosis(
            target="minerva-backend",
            found=True,
            status="exited",
            exit_code=127,
            error=SOCKET_ERROR,
            runtime_error_kind="docker_socket_bind_mount",
            host_docker_socket_ok=True,
            host_docker_socket_detail="/var/run/docker.sock is a Unix socket.",
            compose_service="backend",
        )

        plan = build_remediation_plan(diagnosis, Path("/tmp"))
        commands = [step.command for step in plan.steps if step.command]

        self.assertTrue(plan.safe_to_apply)
        self.assertIn(
            "docker compose up -d --force-recreate backend",
            commands,
        )
        self.assertFalse(any("docker compose build" in command for command in commands))

    def test_unhealthy_host_socket_not_auto_applied(self):
        diagnosis = DockerDiagnosis(
            target="minerva-backend",
            found=True,
            status="exited",
            exit_code=127,
            error=SOCKET_ERROR,
            runtime_error_kind="docker_socket_bind_mount",
            host_docker_socket_ok=False,
            host_docker_socket_detail="socket missing",
            compose_service="backend",
        )

        plan = build_remediation_plan(diagnosis, Path("/tmp"))
        self.assertFalse(plan.safe_to_apply)
        self.assertEqual(plan.steps, [])


if __name__ == "__main__":
    unittest.main()
