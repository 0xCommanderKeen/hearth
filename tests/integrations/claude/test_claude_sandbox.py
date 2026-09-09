"""A Claude run inside the sandbox: the bridge crosses the boundary, and what is left.

The session's own settlement under both launchers is held in
`tests/integrations/test_launcher_parity.py`. What is here is everything that is only
true of a *contained* Claude session: the CLI is the image's copy, the login is a
directory of the run's own with one file it may not change, the shim is started by the
image's interpreter from a configuration mounted at the path Hearth wrote, and the
socket it speaks to is mounted at that same path -- which is the one thing about this
that a Mac cannot do, and a Linux host can (`docs/sandbox.md`, measurement 6).

Nothing here needs a daemon: `tests/fake_docker.py` runs the command it is given and
translates the mounts, so the whole chain -- worker, container argv, fake CLI, real
shim, real unix socket, real transaction -- runs in this process tree.
"""

import json
import os
import signal
import threading
from pathlib import Path

import pytest
from hearth.integrations.claude.config import CREDENTIALS
from hearth.integrations.claude.mcp_bridge import CONFIG_NAME, SOCKET_NAME
from hearth.integrations.claude.subscription import ClaudeLiveRuntime
from hearth.integrations.claude.subscription import worker as claude_worker
from hearth.integrations.launcher import LOGIN, PYTHON, ContainerLauncher, Sandbox
from hearth.residents.journal import Journal
from hearth.residents.memory import Memory
from hearth.residents.models import Refused

from tests import fake_docker
from tests.integrations.claude.test_journal_journey import ENTRY, NOTE
from tests.integrations.claude.test_mcp_bridge import Store, answer
from tests.integrations.test_launcher_parity import DIGEST, IMAGE, NETWORK, daemon, prepared_claude


def sandboxed(tmp_path):
    """A Claude store on the container launcher, and the daemon it was admitted to."""
    docker = daemon(tmp_path)
    return Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker)), docker


def store(tmp_path, **options) -> Store:
    sandbox, _ = sandboxed(tmp_path)
    return Store(tmp_path, sandbox=sandbox, **options)


def session(docker) -> list[str]:
    """The argv of the one container a session ran in, not the one that hashed."""
    started = [
        call for call in fake_docker.calls(docker) if call[:1] == ["run"] and "--cidfile" in call
    ]
    assert len(started) == 1
    return started[0]


def mounts(argv: list[str]) -> dict[str, str]:
    """What the container was given of the host, by the path the session sees."""
    places = {}
    for index, item in enumerate(argv):
        if item != "--mount":
            continue
        fields = dict(part.split("=", 1) for part in argv[index + 1].split(",") if "=" in part)
        places[fields.get("target") or fields.get("destination", "")] = argv[index + 1]
    return places


def facts(runtime, kind: str) -> list[dict]:
    with runtime.database.transaction() as db:
        return [dict(row) for row in db.execute("SELECT * FROM audit WHERE kind=?", (kind,))]


def test_the_second_run_reads_what_the_first_wrote_over_the_bridge_from_a_container(tmp_path):
    """The journal journey again, with every session inside a container of its own.

    The same story as `test_journal_journey.py` and the same assertions about the
    household, except that nothing the session ran with was on this host: the CLI is
    the image's, the configuration is a mount, and the shim reached Hearth's writer
    over a socket mounted into the container at the path Hearth had already written
    into that configuration.
    """
    kitchen = store(tmp_path)
    kitchen.script(
        [
            {
                "tool": "hearth_memory_save",
                "arguments": {
                    "resident_id": "writer",
                    "text": NOTE,
                    "expected_revision": 0,
                    "operation_id": "day-one-note",
                },
            },
            {"tool": "hearth_journal_write", "arguments": {"text": ENTRY}},
        ]
    )
    first = kitchen.run("day-one")
    kitchen.work(first)
    assert kitchen.settle(first).status == "succeeded"

    saved, written = (answer(call["reply"]) for call in kitchen.record()["calls"])
    assert saved["revision"] == 1 and written["run_id"] == first.id
    assert Memory(kitchen.hearth).read("writer")["text"] == NOTE
    assert [row["text"] for row in Journal(kitchen.hearth).read("writer")["entries"]] == [ENTRY]

    # The second day opens with the first day's note, read back over the same bridge.
    kitchen.script([{"tool": "hearth_memory_read", "arguments": {}}], answer_prefix="My note: ")
    second = kitchen.run("day-two")
    kitchen.work(second)
    assert kitchen.settle(second).status == "succeeded"
    read_back = answer(kitchen.record()["calls"][0]["reply"])
    assert read_back["text"] == NOTE and read_back["revision"] == 1

    # Both sessions really ran in a container, and each says which one.
    for run in (first, second):
        receipt = kitchen.runtime.receipt(run.id)
        assert receipt["sandbox"]["launcher"] == "container"
        assert receipt["sandbox"]["image"] == DIGEST
        assert len(receipt["sandbox"]["container_id"]) == 64
        assert receipt["management"]["error"] is None


def test_a_contained_session_is_given_the_image_s_cli_and_a_login_it_cannot_change(tmp_path):
    """One container argv, read as the session reads it."""
    sandbox, docker = sandboxed(tmp_path)
    kitchen = Store(tmp_path, sandbox=sandbox)
    kitchen.script([{"tool": "hearth_journal_write", "arguments": {"text": ENTRY}}])
    run = kitchen.run("only")
    kitchen.work(run)
    assert kitchen.settle(run).status == "succeeded"

    argv = session(docker)
    command = argv[argv.index(IMAGE) + 1 :]
    # The CLI is the image's own copy, never the file on this host.
    assert command[0] == "/usr/local/bin/claude"
    assert str(kitchen.binary) not in command
    folder = kitchen.runtime.folder(run.id)
    # The configuration and the socket it names are one statement, and it says the
    # same thing inside the sandbox and out.
    assert command[command.index("--mcp-config") + 1] == str(folder / CONFIG_NAME)
    places = mounts(argv)
    assert places[str(folder / CONFIG_NAME)].endswith(",readonly")
    assert places[str(folder / SOCKET_NAME)] == (
        f"type=bind,source={folder / SOCKET_NAME},target={folder / SOCKET_NAME}"
    )
    # The login is a directory of the run's own -- a tmpfs, nothing of the host's --
    # with the household's one credential file read-only inside it.
    assert places[LOGIN].startswith("type=tmpfs,")
    assert places[f"{LOGIN}/{CREDENTIALS}"] == (
        f"type=bind,source={kitchen.runtime.config_dir / CREDENTIALS},"
        f"target={LOGIN}/{CREDENTIALS},readonly"
    )
    assert [item for item in argv if item.startswith("CLAUDE_CONFIG_DIR=")] == [
        "CLAUDE_CONFIG_DIR=" + LOGIN
    ]
    # And the name of an account on this host does not cross the boundary: the
    # Keychain is macOS's and a sandbox reads a file.
    assert not [item for item in argv if item.startswith("USER=")]
    # The shim is started by the interpreter the image carries, which is where
    # Hearth's package sits inside the sandbox.
    document = json.loads((folder / CONFIG_NAME).read_text())
    server = document["mcpServers"]["hearth"]
    assert server["command"] == PYTHON
    assert server["args"] == [
        "-I",
        "-m",
        "hearth.integrations.claude.mcp_bridge",
        str(folder / SOCKET_NAME),
    ]
    assert server["env"] == {"PATH": os.defpath}


def test_a_login_that_is_not_a_file_cannot_cross_the_boundary(tmp_path):
    """The macOS Keychain stays a convenience of the process launcher (ADR 0016).

    A configuration directory that answers `loggedIn` and holds no credential file is
    a login this host can use and a sandbox cannot, so the instance refuses to open on
    the container launcher rather than admitting residents whose every run would fail
    at the mount.
    """
    sandbox, _ = sandboxed(tmp_path)
    kitchen = Store(tmp_path, sandbox=sandbox)
    (kitchen.runtime.config_dir / CREDENTIALS).unlink()
    with pytest.raises(Refused, match="claude_subscription_login_required"):
        ClaudeLiveRuntime(
            kitchen.data,
            binary=kitchen.binary,
            config_dir=kitchen.runtime.config_dir,
            sandbox=sandbox,
        )
    # The same store on the process launcher opens: it is the boundary that needs a
    # file, not Hearth.
    assert ClaudeLiveRuntime(
        kitchen.data, binary=kitchen.binary, config_dir=kitchen.runtime.config_dir
    ).kind


def test_cancelling_a_contained_session_stops_the_container_and_keeps_the_stream(tmp_path):
    sandbox, docker = sandboxed(tmp_path)
    runtime, run = prepared_claude(tmp_path / "store", sandbox, docker, pause=5)
    timer = threading.Timer(0.5, runtime.stop, args=(run.id,))
    timer.start()
    try:
        claude_worker(runtime.folder(run.id))
    finally:
        timer.join()
    receipt = runtime.receipt(run.id)
    assert receipt["launched"] is True and receipt["cancelled"] is True
    # The session ended the way a container ends: 128 plus the signal, and the stream
    # it had already written is what settles the run.
    assert receipt["exit_code"] == 128 + int(signal.SIGTERM)
    assert '"session_id"' in receipt["stdout"]
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "cancelled" and evidence.cost is None
    killed = [call for call in fake_docker.calls(docker) if call[0] == "kill"]
    assert killed and killed[-1][-1] == receipt["sandbox"]["container_id"]


def test_a_worker_that_died_leaves_a_container_that_is_stopped_and_never_adopted(tmp_path):
    """A Claude run's stray is ended by the same rule a Codex run's is."""
    sandbox, docker = sandboxed(tmp_path)
    runtime, run = prepared_claude(tmp_path / "store", sandbox, docker)
    launcher = sandbox.open()
    assert isinstance(launcher, ContainerLauncher)
    handle = launcher.start(
        ["sleep", "60"], env={"PATH": os.defpath}, cwd=runtime.folder(run.id), stdin=None
    )
    identity = launcher.identify(handle)
    (runtime.folder(run.id) / "handle.json").write_text(
        json.dumps({"launcher": "container", "id": identity})
    )

    restarted = ClaudeLiveRuntime(
        runtime.data, binary=runtime.binary, config_dir=runtime.config_dir, sandbox=sandbox
    )
    evidence = restarted.inspect(run.id, expected_digest=run.input_digest)
    # Never zero and never adopted: the stream that was being priced died with the
    # worker, so nothing here can say what the session spent (ADR 0008).
    assert evidence.status == "unknown" and evidence.cost is None
    assert launcher.inspect(handle) == "absent"
    removed = facts(restarted, "sandbox.stray_removed")
    assert len(removed) == 1 and removed[0]["resource_id"] == run.id
    assert json.loads(removed[0]["detail"]) == {"launcher": "container", "container": identity}
    # Asked again there is nothing left to remove, so the fact is recorded once.
    assert restarted.inspect(run.id, expected_digest=run.input_digest).status == "unknown"
    assert len(facts(restarted, "sandbox.stray_removed")) == 1


def test_a_run_whose_worker_is_still_here_is_never_called_a_stray(tmp_path):
    """A free lock is not on its own a reason to end somebody's session.

    Measured 2026-09-09: `flock` does not exclude at all on a Docker Desktop bind
    mount from macOS (`docs/sandbox.md`), so the worker's own pid is the second reason
    required before anything is killed.
    """
    sandbox, docker = sandboxed(tmp_path)
    runtime, run = prepared_claude(tmp_path / "store", sandbox, docker)
    launcher = sandbox.open()
    handle = launcher.start(
        ["sleep", "60"], env={"PATH": os.defpath}, cwd=runtime.folder(run.id), stdin=None
    )
    identity = launcher.identify(handle)
    (runtime.folder(run.id) / "handle.json").write_text(
        json.dumps({"launcher": "container", "id": identity, "worker": os.getpid()})
    )
    try:
        assert runtime.inspect(run.id, expected_digest=run.input_digest).status == "unknown"
        assert launcher.inspect(handle) == "running"
        assert facts(runtime, "sandbox.stray_removed") == []
    finally:
        launcher.stop(handle, signal.SIGKILL)
        launcher.wait(handle, 30)


def test_an_image_that_changed_while_the_run_waited_refuses_before_it_launches(tmp_path):
    """The pin a sandboxed run is held to is the image, read inside the guard."""
    sandbox, docker = sandboxed(tmp_path)
    runtime, run = prepared_claude(tmp_path / "store", sandbox, docker)
    with runtime.database.transaction(write=True) as db:
        db.execute(
            "UPDATE system_meta SET value=? WHERE key='sandbox_image'", ("sha256:" + "9" * 64,)
        )
    claude_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    assert receipt["launched"] is False and receipt["cancelled"] is True
    # It ran nowhere, and it still says where it was admitted to run.
    assert receipt["stdout"] == ""
    assert receipt["sandbox"] == {
        "launcher": "container",
        "container_id": None,
        "image": DIGEST,
    }
    assert runtime.inspect(run.id, expected_digest=run.input_digest).cost == 0
    assert [call for call in fake_docker.calls(docker) if "--cidfile" in call] == []


def test_a_receipt_may_not_claim_a_container_a_process_run_never_had(tmp_path):
    """A receipt says where its session ran, and only what Hearth could have written."""
    from hearth.integrations.claude.subscription import encode
    from hearth.integrations.codex.usage import UsageBinding
    from tests.integrations.claude.test_claude_live import BINDING, receipt

    assert encode(receipt(sandbox=None), BINDING)[2].status == "succeeded"
    for claim in (
        {"launcher": "process", "container_id": "a" * 64, "image": None},
        {"launcher": "process", "container_id": None, "image": DIGEST},
        {"launcher": "container", "container_id": None, "image": None},
        {"launcher": "nowhere", "container_id": None, "image": None},
        {"launcher": "container", "container_id": None},
    ):
        with pytest.raises(Refused, match="run_usage_invalid"):
            encode(receipt(sandbox=claim), UsageBinding(**receipt()["binding"]))
