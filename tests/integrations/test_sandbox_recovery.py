"""Terminal attached streams do not release runs while their container is unobserved."""

import json
import os
import signal
import subprocess

import pytest
from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.claude.subscription import worker as claude_worker
from hearth.integrations.codex.subscription import worker as codex_worker
from hearth.integrations.launcher import BINARIES, ContainerLauncher, Sandbox
from hearth.storage.artifacts import Artifacts
from hearth.work.service import Hearth

from tests import fake_docker
from tests.integrations.codex.test_app_server import fake_cli, run_fixture
from tests.integrations.test_launcher_parity import (
    IMAGE,
    NETWORK,
    daemon,
    prepared_claude,
    prepared_codex,
)


def cleanup(docker):
    """Reap synthetic sessions even when the attached fake client was killed."""
    for path in (fake_docker.state(docker) / "containers").glob("*.json"):
        state = json.loads(path.read_text())
        if state["status"] == "running":
            try:
                os.killpg(state["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        path.unlink(missing_ok=True)


def sessions(docker):
    return [call for call in fake_docker.calls(docker) if call[0] == "run" and "--cidfile" in call]


@pytest.mark.parametrize(
    "prepare,worker", [(prepared_codex, codex_worker), (prepared_claude, claude_worker)]
)
@pytest.mark.parametrize("missing_handle", [False, True])
def test_cancelled_stream_waits_for_container_recovery(
    tmp_path, monkeypatch, prepare, worker, missing_handle
):
    docker = daemon(tmp_path)
    sandbox = Sandbox("container", image=IMAGE, network=NETWORK, docker=str(docker))
    runtime, run = prepare(tmp_path / "store", sandbox, docker, pause=60)
    hearth = Hearth(runtime.database)
    execution = Execution(hearth, Artifacts(runtime.data / "artifacts"))
    executor = Executor(execution, runtime)
    offline = False
    original_attempt = ContainerLauncher.attempt
    original_identify = ContainerLauncher.identify

    def attempt(launcher, *arguments, **kwargs):
        if offline:
            return subprocess.CompletedProcess(arguments, 1, "", "Cannot connect to Docker daemon")
        return original_attempt(launcher, *arguments, **kwargs)

    def cancel_after_launch(launcher, handle):
        nonlocal offline
        identity = original_identify(launcher, handle)
        assert identity
        offline = True
        runtime.stop(run.id)
        return identity

    monkeypatch.setattr(ContainerLauncher, "attempt", attempt)
    monkeypatch.setattr(ContainerLauncher, "identify", cancel_after_launch)
    try:
        worker(runtime.folder(run.id))
        receipt = runtime.receipt(run.id)
        assert receipt["launched"] is True and receipt["cancelled"] is True
        identity = receipt["sandbox"]["container_id"]
        container = fake_docker.state(docker) / "containers" / f"{identity}.json"
        assert json.loads(container.read_text())["status"] == "running"
        os.kill(json.loads(container.read_text())["pid"], 0)
        handle_path = runtime.folder(run.id) / "handle.json"
        saved_handle = handle_path.read_bytes()
        if missing_handle:
            handle_path.unlink()
        assert runtime.inspect(run.id, expected_digest=run.input_digest).status == "unknown"
        monkeypatch.setattr(runtime, "start", lambda *a, **k: pytest.fail("session relaunched"))
        executor.step()
        held = hearth.run(run.id)
        assert held.finished_at is None and held.reserved == run.reserved
        with runtime.database.transaction() as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM audit WHERE kind='sandbox.stray_removed'"
                ).fetchone()[0]
                == 0
            )
            assert (
                db.execute("SELECT COUNT(*) FROM run_usage WHERE run_id=?", (run.id,)).fetchone()[0]
                == 0
            )
        offline = False
        if missing_handle:
            # A reachable daemon still cannot prove which container a lost identity meant.
            assert runtime.inspect(run.id, expected_digest=run.input_digest).status == "unknown"
            handle_path.write_bytes(saved_handle)
        executor.step()
        finished = hearth.run(run.id)
        assert finished.status == "cancelled" and finished.finished_at is not None
        assert finished.actual_cost is None and not finished.usage_known
        assert not container.exists()
        assert runtime.receipt(run.id) == receipt
        assert executor.step() == []
        assert len(sessions(docker)) == 1
        with runtime.database.transaction() as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM audit WHERE kind='sandbox.stray_removed'"
                ).fetchone()[0]
                == 1
            )
            assert (
                db.execute("SELECT COUNT(*) FROM run_usage WHERE run_id=?", (run.id,)).fetchone()[0]
                == 1
            )
    finally:
        offline = False
        cleanup(docker)


def test_discovery_with_unknown_termination_never_starts_the_turn(tmp_path, monkeypatch):
    docker = daemon(tmp_path)
    cli = fake_cli(tmp_path)
    fake_docker.carry(docker, BINARIES["codex_live_binary"], cli[0].read_bytes())
    launcher = ContainerLauncher(IMAGE, NETWORK, docker=str(docker))
    original_attempt = launcher.attempt
    original_stop = launcher.stop
    offline = False
    seen = []

    def attempt(*arguments, **kwargs):
        if offline:
            return subprocess.CompletedProcess(arguments, 1, "", "Cannot connect to Docker daemon")
        return original_attempt(*arguments, **kwargs)

    def lose_daemon_on_stop(handle, number):
        nonlocal offline
        offline = True
        return original_stop(handle, number)

    monkeypatch.setattr(launcher, "attempt", attempt)
    monkeypatch.setattr(launcher, "stop", lose_daemon_on_stop)
    try:
        result = run_fixture(
            tmp_path,
            cli=cli,
            launcher=launcher,
            on_session=lambda handle: seen.append(handle.document()),
        )
        assert result["error"] == "sandbox_termination_unknown"
        assert result["launched"] is False
        assert len(sessions(docker)) == 1
        assert len(seen) == 2 and seen[-1]["id"]
    finally:
        offline = False
        cleanup(docker)
