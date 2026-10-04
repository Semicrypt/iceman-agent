from __future__ import annotations

import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from iceman.safety import check_command


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float = 0.0
    interrupted: bool = False


class CommandBlockedError(RuntimeError):
    pass


OutputCallback = Callable[[str, str], None]
HeartbeatCallback = Callable[[float, float], None]


def _argv_for(command: str) -> list[str]:
    safety = check_command(command)
    if not safety.allowed:
        raise CommandBlockedError(safety.reason or "command blocked")
    # Intentionally no shell=True. Shell operators are not interpreted.
    return shlex.split(command)


def run_command(command: str, cwd: Path | None = None) -> CommandResult:
    argv = _argv_for(command)
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=max(0.0, time.monotonic() - started),
    )


def run_command_streaming(
    command: str,
    cwd: Path | None = None,
    *,
    on_output: OutputCallback | None = None,
    on_heartbeat: HeartbeatCallback | None = None,
    heartbeat_interval: float = 2.0,
) -> CommandResult:
    """Run a trusted command while streaming output and emitting silent-process heartbeats.

    Percentages are intentionally not synthesized here. The caller may expose workflow-step
    percentages because those are measurable, while command-level progress is only shown when
    the command itself emits it (for example BuildKit or kubectl rollout status).
    """

    argv = _argv_for(command)
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )

    events: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def pump(name: str, stream) -> None:  # type: ignore[no-untyped-def]
        try:
            for line in iter(stream.readline, ""):
                events.put((name, line))
        finally:
            try:
                stream.close()
            finally:
                events.put((name, None))

    assert proc.stdout is not None
    assert proc.stderr is not None
    stdout_thread = threading.Thread(target=pump, args=("stdout", proc.stdout), daemon=True)
    stderr_thread = threading.Thread(target=pump, args=("stderr", proc.stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    closed_streams = 0
    last_output_at = started
    last_heartbeat_at = started
    interrupted = False

    try:
        while closed_streams < 2 or proc.poll() is None:
            now = time.monotonic()
            wait_for = max(0.05, min(0.25, heartbeat_interval - (now - last_heartbeat_at)))
            try:
                stream_name, payload = events.get(timeout=wait_for)
            except queue.Empty:
                stream_name, payload = "", ""

            if stream_name:
                if payload is None:
                    closed_streams += 1
                else:
                    last_output_at = time.monotonic()
                    if stream_name == "stdout":
                        stdout_parts.append(payload)
                    else:
                        stderr_parts.append(payload)
                    if on_output:
                        on_output(stream_name, payload)

            now = time.monotonic()
            if (
                on_heartbeat
                and proc.poll() is None
                and now - last_heartbeat_at >= heartbeat_interval
            ):
                on_heartbeat(now - started, now - last_output_at)
                last_heartbeat_at = now

        returncode = proc.wait()
    except KeyboardInterrupt:
        interrupted = True
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        returncode = 130
    finally:
        stdout_thread.join(timeout=0.5)
        stderr_thread.join(timeout=0.5)

    # Drain any lines that landed between process exit and thread completion.
    while True:
        try:
            stream_name, payload = events.get_nowait()
        except queue.Empty:
            break
        if payload is None:
            continue
        if stream_name == "stdout":
            stdout_parts.append(payload)
        else:
            stderr_parts.append(payload)
        if on_output:
            on_output(stream_name, payload)

    return CommandResult(
        command=command,
        returncode=returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        duration_seconds=max(0.0, time.monotonic() - started),
        interrupted=interrupted,
    )
