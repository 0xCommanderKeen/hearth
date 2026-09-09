"""A Codex run inside the sandbox: cancelled, interrupted, or left behind entirely.

The happy path is held in `tests/integrations/test_launcher_parity.py`, beside the
same run started as a child of its worker. What is here is everything that goes
wrong afterwards, which is where a sandbox differs from a child: a container outlives
the client that was attached to it, so a worker that dies leaves a session running and
spending, and Hearth has to end it rather than wait for `--rm` to (measured,
`docs/sandbox.md`). Nothing here needs a daemon; `tests/fake_docker.py` runs the
command it is given.
"""

import json
import os
import signal
import threading

import pytest
from hearth.integrations.codex.subscription import CodexLiveRuntime
from hearth.integrations.codex.subscription import worker as codex_worker
from hearth.integrations.launcher import IMAGE_PIN, ContainerLauncher, Sandbox
from hearth.residents.models import Refused

from tests import fake_docker
from tests.integrations.test_launcher_parity import DIGEST, IMAGE, NETWORK, daemon, prepared_codex


def sandboxed(tmp_path, pause: float = 0):
    """One admitted run on the container launcher, and the daemon it was admitted to."""
    docker = daemon(tmp_path)
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker))
    runtime, run = prepared_codex(tmp_path / "store", sandbox, docker, pause)
    return runtime, run, sandbox, docker


def facts(runtime, kind: str) -> list[dict]:
    with runtime.database.transaction() as db:
        rows = db.execute("SELECT * FROM audit WHERE kind=?", (kind,)).fetchall()
    return [dict(row) for row in rows]


def sessions(docker) -> list[list[str]]:
    """Every container started for a session, as opposed to hashing the image."""
    return [
        call for call in fake_docker.calls(docker) if call[:1] == ["run"] and "--cidfile" in call
    ]


def test_cancelling_a_sandboxed_run_stops_the_container_and_settles_what_it_said(tmp_path):
    """The container is signalled by name, and the stream so far is the receipt."""
    runtime, run, _, docker = sandboxed(tmp_path, pause=5)
    timer = threading.Timer(0.5, runtime.stop, args=(run.id,))
    timer.start()
    try:
        codex_worker(runtime.folder(run.id))
    finally:
        timer.join()
    receipt = runtime.receipt(run.id)
    assert receipt["launched"] is True and receipt["cancelled"] is True
    # The session ended the way a container ends: 128 plus the signal, and the
    # stream it had already written is what settles the run.
    assert receipt["exit_code"] == 128 + int(signal.SIGTERM)
    assert '"thread.started"' in receipt["stdout"]
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "cancelled" and evidence.cost is None
    # It was stopped through the runtime, by the id of the container this run ran in,
    # and that id is what the receipt says it ran in.
    killed = [call for call in fake_docker.calls(docker) if call[0] == "kill"]
    assert killed and killed[-1][-1] == receipt["sandbox"]["container_id"]
    assert receipt["sandbox"]["image"] == DIGEST


def test_a_worker_that_died_leaves_a_container_that_is_stopped_and_never_adopted(tmp_path):
    """After a restart the same container is observed, ended and audited."""
    runtime, run, sandbox, docker = sandboxed(tmp_path)
    launcher = sandbox.open()
    assert isinstance(launcher, ContainerLauncher)
    # A session that outlived the worker that started it: the run has a handle and no
    # receipt, and nothing holds its worker lock.
    handle = launcher.start(
        ["sleep", "60"], env={"PATH": os.defpath}, cwd=runtime.folder(run.id), stdin=None
    )
    identity = launcher.identify(handle)
    (runtime.folder(run.id) / "handle.json").write_text(
        json.dumps({"launcher": "container", "id": identity})
    )

    # Hearth restarted: a new adapter over the same data directory, reading the same
    # run folder, asks the runtime about the container the last worker named.
    restarted = CodexLiveRuntime(
        runtime.data, binary=runtime.binary, auth_home=runtime.auth_home, sandbox=sandbox
    )
    evidence = restarted.inspect(run.id, expected_digest=run.input_digest)
    # Never zero and never adopted: the stream that was being priced died with the
    # worker, so nothing here can say what the session spent (ADR 0008).
    assert evidence.status == "unknown" and evidence.cost is None
    assert launcher.inspect(handle) == "absent"
    assert launcher.wait(handle, 30) is not None
    removed = facts(restarted, "sandbox.stray_removed")
    assert len(removed) == 1 and removed[0]["resource_id"] == run.id
    assert json.loads(removed[0]["detail"]) == {"launcher": "container", "container": identity}
    # Nothing was launched to replace it, and asked again there is nothing left to
    # remove, so the fact is recorded once.
    assert len(sessions(docker)) == 1
    assert restarted.inspect(run.id, expected_digest=run.input_digest).status == "unknown"
    assert len(facts(restarted, "sandbox.stray_removed")) == 1


def test_a_run_whose_worker_still_holds_it_is_running_and_is_left_alone(tmp_path):
    """A live session is somebody's work in progress, not a stray."""
    runtime, run, sandbox, docker = sandboxed(tmp_path)
    launcher = sandbox.open()
    handle = launcher.start(
        ["sleep", "60"], env={"PATH": os.defpath}, cwd=runtime.folder(run.id), stdin=None
    )
    identity = launcher.identify(handle)
    (runtime.folder(run.id) / "handle.json").write_text(
        json.dumps({"launcher": "container", "id": identity})
    )
    from hearth.integrations.codex.subscription import worker_lock

    with worker_lock(runtime.folder(run.id)):
        assert runtime.inspect(run.id, expected_digest=run.input_digest).status == "running"
    assert launcher.inspect(handle) == "running"
    assert facts(runtime, "sandbox.stray_removed") == []
    launcher.stop(handle, signal.SIGKILL)
    launcher.wait(handle, 30)


def test_a_session_the_runtime_never_named_leaves_nothing_to_remove(tmp_path):
    """An execution nobody can name is never guessed at, and never invented."""
    runtime, run, sandbox, _ = sandboxed(tmp_path)
    (runtime.folder(run.id) / "handle.json").write_text(
        json.dumps({"launcher": "container", "id": None})
    )
    assert runtime.inspect(run.id, expected_digest=run.input_digest).status == "unknown"
    assert facts(runtime, "sandbox.stray_removed") == []


def test_a_copy_opened_for_reading_never_ends_another_instance_s_session(tmp_path):
    """The containers a restored store names belong to the household it came from."""
    runtime, run, sandbox, docker = sandboxed(tmp_path)
    launcher = sandbox.open()
    handle = launcher.start(
        ["sleep", "60"], env={"PATH": os.defpath}, cwd=runtime.folder(run.id), stdin=None
    )
    identity = launcher.identify(handle)
    (runtime.folder(run.id) / "handle.json").write_text(
        json.dumps({"launcher": "container", "id": identity})
    )
    with runtime.database.transaction(write=True) as db:
        db.execute("INSERT INTO system_meta VALUES ('restore_hold', '1')")
    copy = CodexLiveRuntime(runtime.data, sandbox=sandbox)
    assert copy.database.restored()
    assert copy.inspect(run.id, expected_digest=run.input_digest).status == "unknown"
    assert launcher.inspect(handle) == "running"
    assert facts(copy, "sandbox.stray_removed") == []
    launcher.stop(handle, signal.SIGKILL)
    launcher.wait(handle, 30)


def test_an_image_that_changed_while_the_run_waited_is_refused_before_it_launches(tmp_path):
    """The pin that governs a sandboxed run is the image, and it is checked once."""
    runtime, run, _, docker = sandboxed(tmp_path)
    with runtime.database.transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value=? WHERE key=?", ("sha256:" + "9" * 64, IMAGE_PIN))
    codex_worker(runtime.folder(run.id))
    # Nothing was started, so nothing has to be settled from a session nobody watched:
    # this is the refusal every reader already knows, at zero.
    assert sessions(docker) == []
    receipt = runtime.receipt(run.id)
    assert receipt["launched"] is False and receipt["cancelled"] is True
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "cancelled" and evidence.cost == 0


def test_a_store_that_was_never_configured_for_a_sandbox_launches_nothing(tmp_path):
    """A run admitted to a container this store cannot vouch for does not run."""
    runtime, run, _, docker = sandboxed(tmp_path)
    with runtime.database.transaction(write=True) as db:
        db.execute("DELETE FROM system_meta WHERE key=?", (IMAGE_PIN,))
    codex_worker(runtime.folder(run.id))
    assert sessions(docker) == []
    assert runtime.inspect(run.id, expected_digest=run.input_digest).status == "cancelled"


def test_an_image_that_changes_after_the_launch_never_ends_a_running_session(tmp_path):
    """A session already running is priced from what it really said, not re-checked."""
    runtime, run, _, docker = sandboxed(tmp_path, pause=0.4)

    def repin():
        with runtime.database.transaction(write=True) as db:
            db.execute(
                "UPDATE system_meta SET value=? WHERE key=?", ("sha256:" + "8" * 64, IMAGE_PIN)
            )

    timer = threading.Timer(0.3, repin)
    timer.start()
    try:
        codex_worker(runtime.folder(run.id))
    finally:
        timer.join()
    assert len(sessions(docker)) == 1
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "succeeded" and evidence.output == "A real-model summary."


def test_a_receipt_may_not_claim_a_sandbox_hearth_could_not_have_written(tmp_path):
    from hearth.integrations.codex.subscription import encode
    from hearth.integrations.codex.usage import UsageBinding

    runtime, run, _, _ = sandboxed(tmp_path)
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    bound = UsageBinding(**receipt["binding"])
    assert encode(receipt, bound)[2].status == "succeeded"
    # A run that predates the sandbox says nothing about one and settles as it did.
    assert encode({key: value for key, value in receipt.items() if key != "sandbox"}, bound)[2]
    for broken in (
        {"launcher": "somewhere else", "container_id": None, "image": None},
        {"launcher": "container", "container_id": 1, "image": None},
        {"launcher": "container", "container_id": None},
        "container",
    ):
        with pytest.raises(Refused, match="run_usage_invalid"):
            encode(receipt | {"sandbox": broken}, bound)


def test_the_final_message_leaves_the_sandbox_through_the_one_writable_mount(tmp_path):
    """The CLI's own file is the corroboration of its stream, on both launchers."""
    runtime, run, _, _ = sandboxed(tmp_path)
    codex_worker(runtime.folder(run.id))
    receipt = runtime.receipt(run.id)
    assert receipt["final"] == "A real-model summary."
    assert (runtime.folder(run.id) / "workspace" / "final.md").is_file()
