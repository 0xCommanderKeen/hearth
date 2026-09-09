"""Measure what a container runtime really does to a Hearth session. Never in CI.

The decision in `docs/adr/0016-sandbox-per-run.md` rests on six facts about running a
provider CLI inside a per-run container, and none of them are taken from
documentation. This script measures them against a real daemon -- Docker Desktop's
Linux VM on a Mac is one, and so is any Linux host -- through the very launcher Hearth
ships, and writes the answers as evidence.

    uv run --frozen python scripts/measure-sandbox.py --report docs/evidence/sandbox-<date>.json

What it measures:

1. the daemon and kernel it is talking to;
2. how long a container start costs on top of the CLI's own;
3. whether the CLI's stdout reaches the attached worker as it is written, or in blocks;
4. what stopping the container does to the session's process tree;
5. what happens to a container whose attached parent dies, and what `--rm` leaves;
6. whether a session inside the container can reach a unix socket mounted into it --
   from a host path, and from a volume the daemon owns, which is the shape Hearth runs
   in on a server.

It writes into a temporary directory of its own, starts only containers it labels, and
removes every one of them. It never touches a Hearth data directory and it never needs
a credential. `--image` names the image the measurements run in: any image with
`python3` will do, and the default is a small one, because what is measured is the
runtime's behaviour and not the CLI's.
"""

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from hearth.integrations.launcher import LABEL, ContainerLauncher, Mount  # noqa: E402

DEFAULT_IMAGE = "python:3.14-slim"


def client(docker: str, *arguments: str, timeout: float = 60) -> str:
    result = subprocess.run(
        [docker, *arguments], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"docker {arguments[0]} failed: {result.stderr[:2000]}")
    return result.stdout.strip()


def daemon(docker: str) -> dict:
    """1. What is answering, and on what kernel. Every other answer is about this."""
    version = json.loads(client(docker, "version", "--format", "{{json .}}"))
    info = json.loads(client(docker, "info", "--format", "{{json .}}"))
    return {
        "client_version": version["Client"]["Version"],
        "server_version": version["Server"]["Version"],
        "server_os": version["Server"]["Os"],
        "server_arch": version["Server"]["Arch"],
        "kernel": version["Server"]["KernelVersion"],
        "platform": info.get("OperatingSystem"),
        "storage_driver": info.get("Driver"),
        "host": platform.platform(),
    }


def latency(launcher: ContainerLauncher, workspace: Path, rounds: int = 5) -> dict:
    """2. What a container start costs on top of the process Hearth would run anyway."""
    container, process = [], []
    for _ in range(rounds):
        started = time.monotonic()
        handle = launcher.start(
            ["python3", "-c", "print('up', flush=True)"],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            cwd=workspace,
            stdin=subprocess.DEVNULL,
        )
        assert handle.stdout is not None
        handle.stdout.readline()
        first = time.monotonic() - started
        launcher.wait(handle, 60)
        container.append(round(first, 4))

        started = time.monotonic()
        child = subprocess.Popen(
            [sys.executable, "-c", "print('up', flush=True)"], stdout=subprocess.PIPE
        )
        assert child.stdout is not None
        child.stdout.readline()
        process.append(round(time.monotonic() - started, 4))
        child.wait()
    return {
        "rounds": rounds,
        "container_first_byte_seconds": container,
        "process_first_byte_seconds": process,
        "container_median": sorted(container)[rounds // 2],
        "process_median": sorted(process)[rounds // 2],
    }


def streaming(launcher: ContainerLauncher, workspace: Path) -> dict:
    """3. Does a line written inside the container arrive before the next one is?

    Hearth's worker reads the CLI's stdout as the session runs, prices it and can stop
    it mid-turn. A runtime that buffered the stream until exit would keep the receipt
    but lose every one of those.
    """
    program = (
        "import sys, time\n"
        "for index in range(5):\n"
        "    sys.stdout.write('line %d\\n' % index)\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.3)\n"
    )
    handle = launcher.start(
        ["python3", "-c", program],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        cwd=workspace,
        stdin=subprocess.DEVNULL,
    )
    assert handle.stdout is not None
    started = time.monotonic()
    arrivals = []
    for _ in range(5):
        line = handle.stdout.readline()
        arrivals.append((line.decode().strip(), round(time.monotonic() - started, 3)))
    launcher.wait(handle, 60)
    spread = arrivals[-1][1] - arrivals[0][1]
    return {
        "arrivals": arrivals,
        "spread_seconds": round(spread, 3),
        # Five lines 0.3s apart span more than a second if each one travelled on its
        # own, and arrive together if the runtime held them to the end.
        "unbuffered": spread > 0.9,
    }


def stopping(launcher: ContainerLauncher, workspace: Path, evidence: Path) -> dict:
    """4. What `docker kill` does to the CLI's own process tree inside the container.

    The session Hearth starts is one process that starts more (the Claude CLI runs a
    Node tree). On the host the worker signals the whole process group. Inside a
    container it signals PID 1 and the runtime tears the rest down, so what is
    measured here is whether anything survives it.
    """
    program = (
        "import os, signal, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', '''\n"
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *a: (open('/evidence/child', 'w').write('term'),"
        " sys.exit(0)))\n"
        "sys.stdout.write('child up\\\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
        "'''])\n"
        "signal.signal(signal.SIGTERM, lambda *a: (open('/evidence/parent', 'w')"
        ".write('term'), sys.exit(0)))\n"
        "sys.stdout.write('parent up %d\\n' % child.pid); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    handle = launcher.start(
        ["python3", "-u", "-c", program],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        mounts=(Mount(str(evidence), "/evidence", writable=True),),
    )
    assert handle.stdout is not None
    handle.stdout.readline()
    handle.stdout.readline()
    time.sleep(0.5)
    inside = launcher.client(
        "exec",
        str(handle.id),
        "python3",
        "-c",
        "import os; print(len([name for name in os.listdir('/proc') if name.isdigit()]))",
        timeout=30,
    )
    started = time.monotonic()
    launcher.stop(handle, signal.SIGTERM)
    code = launcher.wait(handle, 60)
    answer = {
        "processes_in_container": int(inside),
        "exit_code_seen_by_the_attached_client": code,
        "seconds_to_exit": round(time.monotonic() - started, 3),
        "sigterm_reached_pid_1": (evidence / "parent").exists(),
        "sigterm_reached_the_child_process": (evidence / "child").exists(),
        "container_after_stop": launcher.inspect(handle),
    }
    # The same stop against a session that does not handle the signal, because that
    # is what a receipt records for an ordinary cancellation and it is the one number
    # the two launchers disagree on.
    unhandled = launcher.start(
        ["python3", "-u", "-c", "import time; print('up', flush=True); time.sleep(60)"],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        cwd=workspace,
        stdin=subprocess.DEVNULL,
    )
    assert unhandled.stdout is not None
    unhandled.stdout.readline()
    launcher.stop(unhandled, signal.SIGTERM)
    answer["exit_code_of_a_session_that_did_not_handle_the_signal"] = launcher.wait(unhandled, 60)
    return answer


def orphaned(launcher: ContainerLauncher, workspace: Path) -> dict:
    """5. A worker that dies mid-session: does its container stop, and is it removed?

    This is the stray a later slice has to clean up, so what it looks like matters:
    whether killing the attached client stops the container, and whether `--rm` ever
    removes it.
    """
    handle = launcher.start(
        ["python3", "-u", "-c", "import time; print('up', flush=True); time.sleep(30)"],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        cwd=workspace,
        stdin=subprocess.DEVNULL,
    )
    assert handle.stdout is not None
    handle.stdout.readline()
    os.killpg(handle.process.pid, signal.SIGKILL)
    handle.process.wait(10)
    time.sleep(2)
    still = launcher.inspect(handle)
    listed = client(
        launcher.docker, "ps", "-a", "--no-trunc", "--filter", "id=" + str(handle.id), "-q"
    )
    answer = {
        "container_after_the_client_was_killed": still,
        "listed_by_the_daemon": bool(listed),
    }
    if still == "running":
        launcher.client("kill", str(handle.id), timeout=60)
        time.sleep(2)
        answer["after_an_explicit_kill"] = launcher.inspect(handle)
        answer["removed_by_rm_after_kill"] = not client(
            launcher.docker, "ps", "-a", "--no-trunc", "--filter", "id=" + str(handle.id), "-q"
        )
    return answer


def socket_reachable(launcher: ContainerLauncher, workspace: Path, directory: Path) -> dict:
    """6. Can the shim inside the container reach Hearth's bridge socket?

    Two shapes, because they are not the same fact. A socket on the *host* filesystem
    reaches the container over whatever the runtime shares host paths with, and on
    Docker Desktop that is a virtual filesystem between macOS and the Linux VM. A
    socket inside a volume the daemon itself owns is the shape Hearth runs in on a
    server, where Hearth is a container and the run folder is a volume.
    """
    answer: dict = {}
    path = directory / "bridge.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    listener.settimeout(30)
    served = []

    def serve():
        try:
            connection, _ = listener.accept()
            connection.recv(16)
            connection.sendall(b"pong")
            connection.close()
            served.append(True)
        except OSError as error:
            answer["host_socket_listener_error"] = repr(error)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        program = (
            "import socket\n"
            "client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
            "try:\n"
            f"    client.connect({str(path)!r})\n"
            "    client.sendall(b'ping')\n"
            "    print('connected', client.recv(16).decode())\n"
            "except OSError as error:\n"
            "    print('refused', type(error).__name__, error)\n"
        )
        command = ["python3", "-u", "-c", program]
        # The launcher's own argv, run directly so that the runtime's complaint is
        # captured instead of discarded: a mount that cannot be made is the answer.
        argv = launcher.arguments(
            command, env={"PATH": "/usr/local/bin:/usr/bin:/bin"}, mounts=(), socket=path
        ) + [launcher.image, *command]
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=120, check=False, cwd=workspace
        )
        answer["host_bind_mount"] = (result.stdout + result.stderr).strip()[:600]
        answer["host_bind_mount_exit_code"] = result.returncode
        answer["host_socket_was_connected_to"] = bool(served)
    finally:
        listener.close()
        thread.join(1)
        path.unlink(missing_ok=True)

    # The server shape: the socket lives in a volume, and both ends are containers.
    volume = "hearth-sandbox-measurement"
    client(launcher.docker, "volume", "create", volume)
    try:
        server = subprocess.Popen(
            [
                launcher.docker,
                "run",
                "--rm",
                "--label",
                LABEL + "=1",
                "--network",
                "none",
                "--mount",
                f"type=volume,source={volume},target=/run/hearth",
                launcher.image,
                "python3",
                "-u",
                "-c",
                "import socket\n"
                "listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
                "listener.bind('/run/hearth/bridge.sock')\n"
                "listener.listen(1)\n"
                "print('listening', flush=True)\n"
                "connection, _ = listener.accept()\n"
                "connection.recv(16)\n"
                "connection.sendall(b'pong')\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert server.stdout is not None
        server.stdout.readline()
        answer["volume_shared_between_containers"] = client(
            launcher.docker,
            "run",
            "--rm",
            "--label",
            LABEL + "=1",
            "--network",
            "none",
            "--mount",
            f"type=volume,source={volume},target=/run/hearth",
            launcher.image,
            "python3",
            "-u",
            "-c",
            "import socket\n"
            "client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
            "try:\n"
            "    client.connect('/run/hearth/bridge.sock')\n"
            "    client.sendall(b'ping')\n"
            "    print('connected', client.recv(16).decode())\n"
            "except OSError as error:\n"
            "    print('refused', type(error).__name__, error)\n",
        )
        server.wait(30)
    finally:
        client(launcher.docker, "volume", "rm", "--force", volume)
    return answer


def binary_hashing(launcher: ContainerLauncher) -> dict:
    """The image check Hearth does at start: hashing a file with the image's own tool."""
    from hearth.integrations.launcher import hashed

    started = time.monotonic()
    value = hashed(launcher, "/usr/local/bin/python3.14")
    return {
        "path": "/usr/local/bin/python3.14",
        "sha256": value,
        "seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--network", default="none")
    arguments = parser.parse_args()
    if arguments.report.exists():
        raise RuntimeError("Report exists; choose a new report path")
    reference = client(arguments.docker, "image", "inspect", "--format", "{{.Id}}", arguments.image)
    launcher = ContainerLauncher(arguments.image, arguments.network, docker=arguments.docker)
    directory = Path(tempfile.mkdtemp(prefix="hearth-measure-"))
    workspace = directory / "workspace"
    workspace.mkdir()
    evidence = directory / "evidence"
    evidence.mkdir()
    report = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "image": arguments.image,
        "image_id": reference,
        "network": arguments.network,
    }
    try:
        report["daemon"] = daemon(arguments.docker)
        report["start_latency"] = latency(launcher, workspace)
        report["streaming"] = streaming(launcher, workspace)
        report["stopping"] = stopping(launcher, workspace, evidence)
        report["orphaned"] = orphaned(launcher, workspace)
        report["bridge_socket"] = socket_reachable(launcher, workspace, directory)
        report["image_binary_hash"] = binary_hashing(launcher)
    finally:
        stray = client(arguments.docker, "ps", "-aq", "--filter", "label=" + LABEL)
        if stray:
            client(arguments.docker, "rm", "--force", *stray.split())
        shutil.rmtree(directory, ignore_errors=True)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
