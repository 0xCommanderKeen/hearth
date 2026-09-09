"""The seam beneath the worker: two launchers, one interface, one pinned image.

The container launcher is driven against `tests/fake_docker.py`, which runs the
command it is given on the host, so the argv Hearth builds, the identity it records
and the signals it sends are all exercised without a daemon. What a real daemon
actually does with that argv is measured instead, against Docker Desktop's Linux VM
(`scripts/measure-sandbox.py`, `docs/sandbox.md`).
"""

import os
import signal
import sys
import time
from pathlib import Path

import pytest
from hearth.integrations.launcher import (
    BINARIES,
    IMAGE_PIN,
    ContainerLauncher,
    Mount,
    ProcessLauncher,
    Sandbox,
    configure,
)
from hearth.residents.models import Refused
from hearth.storage.database import Database

from tests import fake_docker

DIGEST = "sha256:" + "1" * 64
IMAGE = "ghcr.io/hearth/sandbox@" + DIGEST
NETWORK = "hearth-sandbox"


def script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return path


def gone(pid: int) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def container(tmp_path, **held):
    """A container launcher pointed at a fake daemon that holds what the test says."""
    docker = fake_docker.install(tmp_path)
    fake_docker.hold(docker, image=IMAGE, network=NETWORK, **held)
    return ContainerLauncher(IMAGE, NETWORK, docker=str(docker)), docker


# -- the process launcher -------------------------------------------------


def test_the_process_launcher_hands_back_the_child_s_own_stream(tmp_path):
    launcher = ProcessLauncher()
    program = script(tmp_path / "cli", "import sys\nprint('one')\nprint('two')\nsys.exit(3)\n")
    handle = launcher.start([str(program)], env={"PATH": os.defpath}, cwd=tmp_path, stdin=None)
    assert handle.launcher == "process" and handle.id == str(handle.process.pid)
    assert launcher.wait(handle, 10) == 3
    assert handle.stdout is not None and handle.stdout.read() == b"one\ntwo\n"
    assert launcher.inspect(handle) == "exited"
    assert handle.document() == {"launcher": "process", "id": str(handle.process.pid)}


def test_the_process_launcher_refuses_a_mount_list_it_cannot_enforce(tmp_path):
    """A fence nobody holds is worse than no fence: it is refused, not ignored."""
    with pytest.raises(Refused, match="sandbox_mounts_unsupported"):
        ProcessLauncher().start(
            [sys.executable, "-c", "pass"],
            env={"PATH": os.defpath},
            cwd=tmp_path,
            stdin=None,
            mounts=(Mount("/host/notes", "/notes"),),
        )


def test_stopping_a_process_ends_the_tree_it_started_not_only_its_first_pid(tmp_path):
    launcher = ProcessLauncher()
    program = script(
        tmp_path / "cli",
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\ntime.sleep(60)\n",
    )
    handle = launcher.start([str(program)], env={"PATH": os.defpath}, cwd=tmp_path, stdin=None)
    assert handle.stdout is not None
    grandchild = int(handle.stdout.readline())
    assert launcher.inspect(handle) == "running"
    launcher.stop(handle, signal.SIGKILL)
    assert launcher.wait(handle, 10) is not None
    assert gone(grandchild)
    # Signalling a session that has already ended is not an error anywhere.
    launcher.stop(handle, signal.SIGKILL)


def test_waiting_past_the_timeout_answers_nothing_rather_than_raising(tmp_path):
    launcher = ProcessLauncher()
    handle = launcher.start(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={"PATH": os.defpath},
        cwd=tmp_path,
        stdin=None,
    )
    try:
        assert launcher.wait(handle, 0.2) is None
    finally:
        launcher.stop(handle, signal.SIGKILL)
        launcher.wait(handle, 10)


# -- the container launcher -----------------------------------------------


def test_the_sandbox_argv_names_every_fence_the_decision_records(tmp_path):
    launcher, _ = container(tmp_path)
    argv = launcher.arguments(
        ["/usr/local/bin/claude", "--print"],
        env={"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/login"},
        mounts=(Mount("/host/notes", "/notes"),),
        socket=Path("/run/folder/bridge.sock"),
    )
    assert argv[1] == "run"
    for flag in ("--rm", "--init", "--read-only", "--interactive"):
        assert flag in argv
    assert argv[argv.index("--pull") + 1] == "never"
    assert argv[argv.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert argv[argv.index("--network") + 1] == NETWORK
    assert argv[argv.index("--workdir") + 1] == "/workspace"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges=true" in argv
    assert any(part.startswith("/workspace:rw,noexec") for part in argv)
    # A granted folder is read-only unless the grant said otherwise; the bridge
    # socket is mounted at the very path the session's configuration names.
    assert "type=bind,source=/host/notes,target=/notes,readonly" in argv
    assert "type=bind,source=/run/folder/bridge.sock,target=/run/folder/bridge.sock" in argv
    assert "PATH=/usr/bin" in argv and "CLAUDE_CONFIG_DIR=/login" in argv


@pytest.mark.parametrize(
    "change",
    [
        {"env": {"PATH; rm -rf": "/usr/bin"}},
        {"env": {"PATH": "/usr/bin\0"}},
        {"command": []},
        {"mounts": (Mount("/host", "/target"),)},
    ],
)
def test_nothing_reaches_the_container_runtime_s_command_line_unparsed(tmp_path, change):
    launcher, _ = container(tmp_path)
    arguments = {
        "command": ["/usr/local/bin/claude"],
        "env": {"PATH": "/usr/bin"},
        "mounts": (),
        "socket": None,
    } | change
    if change.get("mounts"):
        # A valid mount is not a refusal; the refusable shapes are the paths
        # themselves, which `Mount` rejects before a launcher ever sees them.
        assert launcher.arguments(arguments.pop("command"), **arguments)
        for source, target in (("relative", "/t"), ("/a,b", "/t"), ("/a", "b")):
            with pytest.raises(Refused, match="sandbox_mount_invalid"):
                Mount(source, target)
        return
    with pytest.raises(Refused):
        launcher.arguments(arguments.pop("command"), **arguments)


def test_a_sandboxed_session_streams_back_and_is_named_by_its_container(tmp_path):
    launcher, docker = container(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    program = script(tmp_path / "cli", "import sys\nprint('hello', flush=True)\nsys.exit(0)\n")
    handle = launcher.start([str(program)], env={"PATH": os.defpath}, cwd=workspace, stdin=None)
    assert handle.launcher == "container"
    assert handle.id is not None and len(handle.id) == 64
    assert launcher.wait(handle, 30) == 0
    assert handle.stdout is not None and handle.stdout.read() == b"hello\n"
    assert launcher.inspect(handle) == "exited"
    # The id the runtime wrote is the id Hearth recorded, and it is the id every
    # later question about that session is asked with. Nothing of the launch is left
    # behind in the session's own working directory, which a provider's own adapter
    # may require to be empty.
    started = [call for call in fake_docker.calls(docker) if call[0] == "run"]
    assert started and Path(started[0][started[0].index("--cidfile") + 1]).parent != workspace
    assert list(workspace.iterdir()) == []


def test_stopping_a_sandbox_goes_through_the_runtime_and_reports_the_signal(tmp_path):
    """A signalled container reports 128 plus the signal, never a negative code.

    Measured against Docker Desktop on 2026-09-09 and held by the fake, because it
    is the one place the two launchers' receipts differ (`docs/sandbox.md`).
    """
    launcher, _ = container(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    handle = launcher.start(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env={"PATH": os.defpath},
        cwd=workspace,
        stdin=None,
    )
    assert launcher.inspect(handle) == "running"
    launcher.stop(handle, signal.SIGKILL)
    assert launcher.wait(handle, 30) == 128 + int(signal.SIGKILL)


def test_a_session_the_runtime_never_named_is_not_guessed_at(tmp_path, monkeypatch):
    launcher, _ = container(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("hearth.integrations.launcher.IDENTITY_TIMEOUT", 0.5)
    # A client that fails before it creates anything writes no id, and an execution
    # nobody can name is never observed or adopted.
    monkeypatch.setattr(launcher, "docker", sys.executable)
    handle = launcher.start(["-c", "raise SystemExit(1)"], env={}, cwd=workspace, stdin=None)
    assert handle.id is None and handle.document() == {"launcher": "container", "id": None}
    assert launcher.inspect(handle) == "unknown"
    launcher.stop(handle, signal.SIGKILL)
    assert launcher.wait(handle, 30) is not None

    # And a client that is still running while having named nothing is still stopped,
    # because a worker that cannot end what it started would wait on it forever.
    handle = launcher.start(
        ["-c", "import time; time.sleep(60)"], env={}, cwd=workspace, stdin=None
    )
    assert handle.id is None
    launcher.stop(handle, signal.SIGKILL)
    assert launcher.wait(handle, 30) is not None


# -- the configuration that names one of them -----------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"launcher": "podman"},
        {"launcher": "process", "image": IMAGE},
        {"launcher": "process", "network": NETWORK},
        {"launcher": "container", "network": NETWORK},
        {"launcher": "container", "image": IMAGE},
        {"launcher": "container", "image": "hearth/sandbox:latest", "network": NETWORK},
        {"launcher": "container", "image": IMAGE, "network": "not a network"},
        {"launcher": "container", "image": IMAGE, "network": NETWORK, "docker": "../docker"},
    ],
)
def test_a_sandbox_configuration_that_cannot_be_held_to_is_refused(values):
    with pytest.raises(Refused, match="sandbox_configuration_invalid"):
        Sandbox(**values)


def test_the_pin_is_the_bytes_the_reference_names_not_the_name(tmp_path):
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK)
    assert sandbox.digest == DIGEST
    # The same bytes under another repository are the same sandbox.
    assert Sandbox("container", image="mirror/local@" + DIGEST, network=NETWORK).digest == DIGEST
    assert Sandbox().digest is None


def test_the_sandbox_survives_the_request_document_it_travels_in():
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK, docker="/usr/bin/podman")
    assert Sandbox.of(sandbox.document()) == sandbox
    # A run admitted before this seam existed carries no sandbox and ran as a child
    # of its worker; it is read as exactly that and never as something else.
    assert Sandbox.of(None) == Sandbox()
    for broken in ({"launcher": "container"}, {"launcher": 1}, "process", 7):
        with pytest.raises(Refused, match="sandbox_configuration_invalid"):
            Sandbox.of(broken)


def test_the_environment_names_the_launcher_and_defaults_to_today_s():
    assert Sandbox.from_environment({}) == Sandbox()
    assert Sandbox.from_environment({"HEARTH_SANDBOX": ""}) == Sandbox()
    assert Sandbox.from_environment(
        {
            "HEARTH_SANDBOX": "container",
            "HEARTH_SANDBOX_IMAGE": IMAGE,
            "HEARTH_SANDBOX_NETWORK": NETWORK,
        }
    ) == Sandbox("container", image=IMAGE, network=NETWORK)
    with pytest.raises(Refused, match="sandbox_configuration_invalid"):
        Sandbox.from_environment({"HEARTH_SANDBOX": "container"})


def store(tmp_path, **pins) -> Database:
    database = Database(tmp_path / "data" / "hearth.db")
    database.initialize()
    with database.transaction(write=True) as db:
        for key, value in pins.items():
            db.execute("INSERT INTO system_meta VALUES (?,?)", (key, value))
    return database


def test_the_process_launcher_pins_nothing_and_asks_no_daemon(tmp_path):
    database = store(tmp_path)
    assert configure(database, Sandbox()) == {"launcher": "process"}
    with database.transaction() as db:
        assert db.execute("SELECT * FROM system_meta WHERE key=?", (IMAGE_PIN,)).fetchone() is None


def test_a_sandbox_is_pinned_by_digest_and_a_different_image_refuses(tmp_path):
    database = store(tmp_path)
    launcher, docker = container(tmp_path)
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker))
    assert configure(database, sandbox, lambda: 1_788_640_000) == {
        "launcher": "container",
        "image": DIGEST,
    }
    with database.transaction() as db:
        assert (
            db.execute("SELECT value FROM system_meta WHERE key=?", (IMAGE_PIN,)).fetchone()[0]
            == DIGEST
        )
        recorded = db.execute(
            "SELECT resource_id, detail FROM audit WHERE kind='sandbox.configured'"
        ).fetchall()
    assert [row[0] for row in recorded] == ["container"]
    assert DIGEST in recorded[0][1]
    # Starting again on the same image is the ordinary case and records nothing new.
    assert configure(database, sandbox)["image"] == DIGEST
    other = "sha256:" + "2" * 64
    moved = Sandbox(
        "container", image="ghcr.io/hearth/sandbox@" + other, network=NETWORK, docker=str(docker)
    )
    fake_docker.hold(docker, image=moved.image)
    with pytest.raises(Refused, match="sandbox_image_changed"):
        configure(database, moved)


def test_an_image_a_network_or_a_daemon_that_is_missing_refuses_by_name(tmp_path):
    database = store(tmp_path)
    docker = fake_docker.install(tmp_path)
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker))
    with pytest.raises(Refused, match="sandbox_image_unavailable"):
        configure(database, sandbox)
    fake_docker.hold(docker, image=IMAGE)
    with pytest.raises(Refused, match="sandbox_network_missing"):
        configure(database, sandbox)
    absent = Sandbox("container", image=IMAGE, network=NETWORK, docker="/nonexistent/docker")
    with pytest.raises(Refused, match="sandbox_runtime_unavailable"):
        configure(database, absent)
    # Nothing was pinned by any of those refusals.
    with database.transaction() as db:
        assert db.execute("SELECT * FROM system_meta WHERE key=?", (IMAGE_PIN,)).fetchone() is None


def test_the_clis_inside_the_image_have_to_be_the_ones_this_store_is_pinned_to(tmp_path):
    launcher, docker = container(tmp_path)
    pin = fake_docker.carry(docker, BINARIES["codex_live_binary"], b"the pinned codex")
    database = store(tmp_path, codex_live_binary=pin)
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker))
    assert configure(database, sandbox)["image"] == DIGEST

    other = tmp_path / "other"
    other.mkdir()
    docker = fake_docker.install(other)
    fake_docker.hold(docker, image=IMAGE, network=NETWORK)
    fake_docker.carry(docker, BINARIES["codex_live_binary"], b"a different codex")
    with pytest.raises(Refused, match="sandbox_binary_mismatch"):
        configure(
            store(tmp_path / "second", codex_live_binary=pin),
            Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker)),
        )
    # An image that does not carry the CLI at all is the same refusal, not a pass.
    third = tmp_path / "third"
    third.mkdir()
    docker = fake_docker.install(third)
    fake_docker.hold(docker, image=IMAGE, network=NETWORK)
    with pytest.raises(Refused, match="sandbox_binary_mismatch"):
        configure(
            store(tmp_path / "fourth", codex_live_binary=pin),
            Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker)),
        )
