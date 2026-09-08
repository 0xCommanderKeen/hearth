"""Detached worker startup remains observable before Python reaches worker code."""

import json
import os
import subprocess
import sys

import pytest
from hearth.execution.context import read_context
from hearth.execution.lifecycle import Execution
from hearth.integrations.codex.subscription import CodexLiveRuntime
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth


def configured(tmp_path):
    binary = tmp_path / "synthetic-codex"
    binary.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.153.4')\n sys.exit()\n"
        "sys.exit(1)\n"
    )
    binary.chmod(0o700)
    auth = tmp_path / "synthetic-auth"
    auth.mkdir()
    (auth / "auth.json").write_text("{}")
    data = tmp_path / "data"
    database = Database(data / "hearth.db")
    database.initialize()
    runtime = CodexLiveRuntime(data, binary=binary, auth_home=auth)
    observer = CodexLiveRuntime(data, binary=binary, auth_home=auth)
    hearth = Hearth(database)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic startup", 1000000), expected_revision=0
    )
    task = hearth.submit(
        "startup", "reader", "Read synthetic notes", expires_at=int(hearth.clock()) + 60
    )
    run = hearth.admit(task.task_id, reserve=100000)
    Execution(hearth, Artifacts(data / "artifacts")).prepare_start(run.id, run.owner_token)
    with database.transaction() as db:
        prompt = json.dumps(
            read_context(db, run.id, Memory(hearth).files), sort_keys=True, separators=(",", ":")
        )
    return runtime, observer, run, prompt


@pytest.mark.parametrize("exit_before_worker", [False, True])
def test_start_hands_off_live_claim_before_worker_code_and_never_relaunches(
    tmp_path, monkeypatch, exit_before_worker
):
    runtime, observer, run, prompt = configured(tmp_path)
    reader, writer = os.pipe()
    popen = subprocess.Popen
    children = []

    def delayed_worker(args, **kwargs):
        # A real child is deliberately gated before importing the worker. No timing sleeps.
        kwargs["pass_fds"] = (*kwargs.get("pass_fds", ()), reader)
        child = popen(
            [
                sys.executable,
                "-I",
                "-c",
                "import os,sys; fd=int(sys.argv[1]); os.read(fd,1); os.close(fd); "
                "os.execv(sys.argv[2],sys.argv[2:])",
                str(reader),
                *args,
            ],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr("hearth.integrations.codex.subscription.subprocess.Popen", delayed_worker)
    try:
        runtime.start(run.id, prompt)
        assert observer.inspect(run.id, expected_digest=run.input_digest).status == "running"
        runtime.start(run.id, prompt)
        assert len(children) == 1
        if exit_before_worker:
            children[0].kill()
        else:
            os.write(writer, b"go")
        children[0].wait(timeout=10)
        assert observer.inspect(run.id).status == ("unknown" if exit_before_worker else "failed")
        runtime.start(run.id, prompt)
        assert len(children) == 1
    finally:
        os.close(reader)
        os.close(writer)
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def test_spawn_failure_releases_claim_and_remains_unknown_without_retry(tmp_path, monkeypatch):
    runtime, observer, run, prompt = configured(tmp_path)
    attempts = []

    def fail_spawn(*args, **kwargs):
        attempts.append(args)
        raise OSError("synthetic process creation failure")

    monkeypatch.setattr("hearth.integrations.codex.subscription.subprocess.Popen", fail_spawn)
    with pytest.raises(OSError, match="synthetic process creation failure"):
        runtime.start(run.id, prompt)
    assert observer.inspect(run.id).status == "unknown"
    runtime.start(run.id, prompt)
    assert len(attempts) == 1
    assert observer.inspect(run.id).status == "unknown"


def test_worker_rejects_inherited_descriptor_for_another_file(tmp_path):
    from hearth.integrations.codex.subscription import worker
    from hearth.residents.models import Refused

    folder = tmp_path / "worker"
    folder.mkdir()
    (folder / "worker.lock").touch()
    unrelated = tmp_path / "unrelated-lock"
    fd = os.open(unrelated, os.O_RDWR | os.O_CREAT, 0o600)
    with pytest.raises(Refused, match="codex_worker_lock_invalid"):
        worker(folder, fd)
    assert not (folder / "started.json").exists()
