"""The same run, started by either launcher, settling to the same receipt.

Both live adapters are driven end to end -- the real detached worker, the real fake
CLI, the real stream -- once as a child of the worker and once through a container the
fake `docker` starts. What comes back has to be the same evidence: the same stream,
the same answer, the same price. Where the two really differ is the exit code of a
session that was signalled, and that difference is asserted here rather than papered
over, because it is what a real daemon does (`docs/sandbox.md`).
"""

import hashlib
import json
import sys
import threading
from pathlib import Path

from hearth.integrations.codex.subscription import CodexLiveRuntime
from hearth.integrations.codex.subscription import encode as codex_encode
from hearth.integrations.codex.subscription import worker as codex_worker
from hearth.integrations.codex.usage import UsageBinding
from hearth.integrations.launcher import LOGIN, OUTPUT, Sandbox
from hearth.residents.models import Declaration

from tests import fake_docker
from tests.integrations.claude.test_claude_live import prepared as claude_prepared

DIGEST = "sha256:" + "3" * 64
IMAGE = "ghcr.io/hearth/sandbox@" + DIGEST
NETWORK = "hearth-sandbox"
# What the receipt says about the session itself, as opposed to which run it settles.
# The binding and the binary pin name a store and a CLI on disk, and two stores never
# admit the same run id; everything else is the evidence.
EVIDENCE = ("kind", "stdout", "final", "exit_code", "cancelled", "launched")

CODEX_EVENTS = [
    {"type": "thread.started", "thread_id": "parity"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"type": "agent_message", "id": "answer", "text": "A real-model summary."},
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 10194,
            "cached_input_tokens": 6912,
            "output_tokens": 75,
            "reasoning_output_tokens": 0,
            "cache_write_input_tokens": 0,
        },
    },
]


def daemon(tmp_path) -> Path:
    """A fake daemon holding the image and the network an operator would have made."""
    directory = tmp_path / "daemon"
    directory.mkdir(parents=True)
    docker = fake_docker.install(directory)
    fake_docker.hold(docker, image=IMAGE, network=NETWORK)
    return docker


def pinned(database, sandbox, docker, name: str, binary: Path) -> None:
    """Put this store's own CLI inside the fake image, and pin the store to the image.

    Both halves are what a real start does: the image carries the very CLI the store
    is pinned to -- which `configure` checks before any resident is admitted, and
    which the container then executes instead of anything on the host -- and the
    image's digest is written to `system_meta` as the store's own.
    """
    from hearth.integrations.launcher import BINARIES, configure

    fake_docker.carry(docker, BINARIES[name + "_live_binary"], binary.read_bytes())
    configure(database, sandbox)


def sandboxes(tmp_path):
    """Today's launcher, and the container launcher over a fake daemon."""
    docker = daemon(tmp_path)
    return {
        "process": (Sandbox(), None),
        "container": (
            Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker)),
            docker,
        ),
    }


# -- the Claude adapter ---------------------------------------------------


def prepared_claude(tmp_path, sandbox, docker=None, pause: float = 0):
    """A store with one admitted Claude run, started, its worker left to be driven."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime, _, run, _ = claude_prepared(tmp_path, pause=pause, sandbox=sandbox)
    if docker is not None:
        pinned(runtime.database, sandbox, docker, "claude", runtime.binary)
    return runtime, run


def test_a_claude_session_settles_the_same_whichever_launcher_started_it(tmp_path):
    from hearth.integrations.claude.subscription import worker as claude_worker

    receipts, evidence = {}, {}
    for name, (sandbox, docker) in sandboxes(tmp_path).items():
        runtime, run = prepared_claude(tmp_path / name, sandbox, docker)
        claude_worker(runtime.folder(run.id))
        receipts[name] = runtime.receipt(run.id)
        evidence[name] = runtime.inspect(run.id, expected_digest=run.input_digest)
        # What was started is recorded beside the receipt, named by its launcher.
        handle = json.loads((runtime.folder(run.id) / "handle.json").read_text())
        assert handle["launcher"] == name and handle["id"]
    assert set(receipts["process"]) == set(receipts["container"])
    for field in ("kind", "stdout", "exit_code", "cancelled", "launched"):
        assert receipts["process"][field] == receipts["container"][field]
    assert evidence["process"] == evidence["container"]
    assert evidence["process"].status == "succeeded" and evidence["process"].cost == 36_580
    # The one thing the two receipts do not share: where the session ran.
    assert receipts["process"]["sandbox"] == {
        "launcher": "process",
        "container_id": None,
        "image": None,
        # This resident holds no filesystem grant, so there is nothing to name -- said
        # by both launchers in the same words.
        "mounts": [],
    }
    assert receipts["container"]["sandbox"]["launcher"] == "container"
    assert receipts["container"]["sandbox"]["image"] == DIGEST
    assert receipts["container"]["sandbox"]["mounts"] == []
    assert len(receipts["container"]["sandbox"]["container_id"]) == 64


def test_cancelling_a_sandboxed_claude_session_stops_it_and_is_never_free(tmp_path):
    from hearth.integrations.claude.subscription import worker as claude_worker

    endings = {}
    for name, (sandbox, docker) in sandboxes(tmp_path).items():
        runtime, run = prepared_claude(tmp_path / name, sandbox, docker, pause=5)
        timer = threading.Timer(0.5, runtime.stop, args=(run.id,))
        timer.start()
        try:
            claude_worker(runtime.folder(run.id))
        finally:
            timer.join()
        evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
        assert evidence.status == "cancelled" and evidence.cost is None
        published = runtime.receipt(run.id)
        assert published["launched"] is True and published["cancelled"] is True
        endings[name] = published["exit_code"]
    # The one place the launchers differ, and the reason the parity above is asserted
    # on the evidence rather than on every byte: a child reports the negative of the
    # signal that ended it, a container reports 128 plus that signal. Both are "not
    # zero", which is all any reader of the receipt asks.
    assert endings["process"] is not None and endings["process"] < 0
    assert endings["container"] == 128 - endings["process"]


# -- the Codex adapter ----------------------------------------------------


def codex_cli(path: Path, pause: float = 0, writes: bool = False) -> Path:
    """A `codex` that answers the version probe, reads its prompt and replays a turn.

    With a pause it stops mid-stream, having already said something, which is what a
    cancellation has to settle from. With `writes` it puts a file in every writable
    folder its prompt says it was granted -- by the host path, because the fake daemon
    runs the command it is given rather than mounting anything, so `/mounts/<name>`
    exists in the argv and nowhere else.
    """
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.153.4')\n sys.exit()\n"
        "prompt = sys.stdin.read()\n"
        "assert prompt, 'the session was launched with no prompt'\n"
        "final = sys.argv[sys.argv.index('-o') + 1]\n"
        + (
            "for mount in json.loads(prompt).get('mounts', []):\n"
            "    if mount['mode'] == 'rw':\n"
            "        open(mount['host_path'] + '/written-by-the-session.md', 'w').write('done')\n"
            if writes
            else ""
        )
        + f"for event in {json.dumps(CODEX_EVENTS)}:\n"
        "    sys.stdout.write(json.dumps(event) + '\\n')\n"
        "    sys.stdout.flush()\n"
        f"    time.sleep({float(pause)})\n"
        "open(final, 'w').write('A real-model summary.')\n"
    )
    path.chmod(0o700)
    return path


def prepared_codex(tmp_path, sandbox, docker=None, pause: float = 0, mounts=(), writes=False):
    """A store with one admitted Codex run, started, its worker left to be driven.

    `mounts` is the filesystem grant its resident holds when the run is admitted, in the
    shape an operator writes (`docs/adr/0016-sandbox-per-run.md`).
    """
    from unittest.mock import patch

    from hearth.execution.context import read_context
    from hearth.execution.lifecycle import Execution
    from hearth.residents.memory import Memory
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.database import Database
    from hearth.work.service import Hearth

    data = tmp_path / "data"
    auth = tmp_path / "login"
    auth.mkdir(parents=True)
    (auth / "auth.json").write_text("synthetic-only")
    database = Database(data / "hearth.db")
    database.initialize()
    binary = codex_cli(tmp_path / "codex", pause, writes)
    runtime = CodexLiveRuntime(data, binary=binary, auth_home=auth, sandbox=sandbox)
    if docker is not None:
        pinned(database, sandbox, docker, "codex", binary)
    hearth = Hearth(database)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    if mounts:
        from hearth.management.authority import Management

        Management(hearth).save(
            "reader", {"expected_revision": 0, "mounts": [dict(mount) for mount in mounts]}
        )
    task = hearth.submit("live", "reader", "Summarize", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(task.task_id, reserve=100_000)
    Execution(hearth, Artifacts(data / "artifacts")).prepare_start(run.id, run.owner_token)
    with database.transaction() as db:
        prompt = json.dumps(
            read_context(db, run.id, Memory(hearth).files), sort_keys=True, separators=(",", ":")
        )
    with patch("hearth.integrations.codex.subscription.subprocess.Popen", lambda *a, **k: None):
        runtime.start(run.id, prompt)
    return runtime, run


def test_a_codex_session_settles_the_same_whichever_launcher_started_it(tmp_path):
    receipts, evidence = {}, {}
    for name, (sandbox, docker) in sandboxes(tmp_path).items():
        runtime, run = prepared_codex(tmp_path / name, sandbox, docker)
        codex_worker(runtime.folder(run.id))
        receipts[name] = runtime.receipt(run.id)
        bound = UsageBinding(**receipts[name]["binding"])
        evidence[name] = codex_encode(receipts[name], bound)[2]
        handle = json.loads((runtime.folder(run.id) / "handle.json").read_text())
        assert handle["launcher"] == name and handle["id"]
    assert set(receipts["process"]) == set(receipts["container"])
    for field in EVIDENCE:
        assert receipts["process"][field] == receipts["container"][field]
    assert evidence["process"] == evidence["container"]
    assert evidence["process"].status == "succeeded" and evidence["process"].cost == 43_482
    assert evidence["process"].output == "A real-model summary."
    # The one thing the two receipts do not share: where the session ran. A run that
    # was a child of its worker names no container and no image; a sandboxed one names
    # both, so a finished run says where it happened whatever configuration says now.
    assert receipts["process"]["sandbox"] == {
        "launcher": "process",
        "container_id": None,
        "image": None,
        "mounts": [],
    }
    assert receipts["container"]["sandbox"] == {
        "launcher": "container",
        "container_id": receipts["container"]["sandbox"]["container_id"],
        "image": DIGEST,
        "mounts": [],
    }
    assert len(receipts["container"]["sandbox"]["container_id"]) == 64
    # The final message came back out of the sandbox, so both launchers corroborate
    # the stream's own answer with the file the CLI wrote.
    assert receipts["container"]["final"] == "A real-model summary."


def test_a_sandboxed_session_is_the_bounded_command_the_adapter_built(tmp_path):
    """The argv inside the container is the CLI's own, unchanged by the launcher."""
    docker = daemon(tmp_path)
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker))
    runtime, run = prepared_codex(tmp_path / "store", sandbox, docker)
    codex_worker(runtime.folder(run.id))
    # The session's own container, told apart from the one that hashed the image.
    started = [
        call for call in fake_docker.calls(docker) if call[:1] == ["run"] and "--cidfile" in call
    ]
    assert len(started) == 1
    argv = started[0]
    command = argv[argv.index(IMAGE) + 1 :]
    assert command[1] == "exec" and command[-1] == "-"
    assert "--ignore-user-config" in command and "--json" in command
    # Every path in it is a path inside the image: the CLI is the image's own copy,
    # the login is the read-only mount, the final message is written into the one
    # directory this session may write to, and nothing names a directory on the host.
    assert command[0] == "/usr/local/bin/codex"
    assert command[command.index("-o") + 1] == OUTPUT + "/final.md"
    assert "CODEX_HOME=" + LOGIN in argv
    folder = runtime.folder(run.id)
    # The configuration directory is the session's own and dies with it; the one file
    # in it that is the household's is read-only (measured, `docs/sandbox.md`).
    assert f"type=tmpfs,destination={LOGIN},tmpfs-size=64m" in argv
    credential = f"type=bind,source={runtime.auth_home / 'auth.json'},target={LOGIN}/auth.json"
    assert credential + ",readonly" in argv
    assert f"type=bind,source={folder / 'workspace'},target={OUTPUT}" in argv
    assert not any(str(folder) in part for part in command)
    # And it really wrote through that mount: the file the worker reads is on the host.
    assert (folder / "workspace" / "final.md").read_text() == "A real-model summary."
    # And the run's evidence knows the container it ran in, by the id the runtime gave.
    handle = json.loads((runtime.folder(run.id) / "handle.json").read_text())
    # What was started, and who was holding it: the two things a later observation
    # asks before it decides a session has been left behind.
    assert handle == {
        "launcher": "container",
        "id": handle["id"],
        "cidfile": None,
        "worker": handle["worker"],
    }
    assert len(handle["id"]) == 64 and handle["worker"] > 0
    assert (
        hashlib.sha256(runtime.receipt(run.id)["stdout"].encode()).hexdigest()
        == hashlib.sha256(
            "".join(json.dumps(event) + "\n" for event in CODEX_EVENTS).encode()
        ).hexdigest()
    )
