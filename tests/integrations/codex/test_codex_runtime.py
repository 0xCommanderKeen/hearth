"""Application-level recovery from an uncertain split-container cleanup."""

import json
from dataclasses import asdict

from hearth.execution.context import read_context
from hearth.execution.lifecycle import Execution
from hearth.integrations.codex import assets as codex_assets
from hearth.integrations.codex import runtime as codex_runtime
from hearth.integrations.codex.container import CodexContainer
from hearth.integrations.codex.runtime import CodexMockRuntime
from hearth.integrations.codex.usage import UsageBinding
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.integrations.codex.test_codex_subscription_probe import Docker


def test_reopened_runtime_finishes_cleanup_before_returning_saved_settlement(tmp_path, monkeypatch):
    database = Database(tmp_path / "hearth.db")
    database.initialize(runtime_kind="codex_mock")
    hearth = Hearth(database, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    task = hearth.submit("request", "reader", "Synthetic summary", expires_at=1_788_640_100)
    run = hearth.admit(task.task_id, reserve=1000)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    execution.prepare_start(run.id, run.owner_token)
    monkeypatch.setattr(codex_assets, "prepare", lambda *args: "a" * 64)
    monkeypatch.setattr(codex_runtime.threading.Thread, "start", lambda self: None)
    docker = Docker()
    runtime = CodexMockRuntime(tmp_path, docker=docker)
    with database.transaction() as db:
        context = read_context(db, run.id, Memory(hearth).files)
    # The runtime input is the exact persisted context used for the launch digest.
    prompt = json.dumps(context, sort_keys=True, separators=(",", ":"))
    runtime.start(run.id, prompt)
    request = runtime._request(run.id)
    binding = UsageBinding(**request["binding"])
    assert asdict(binding) == request["binding"]
    folder = runtime.folder(run.id)
    container = CodexContainer.create(
        folder / "collector",
        binding,
        role="collector",
        name="hearth-codex-recovery",
        mounts=[
            (tmp_path / "fixture", "/probe.py", False),
            (tmp_path / "app", "/app", False),
            (tmp_path / "journal", "/journal", True),
            (tmp_path / "secret", "/collector-secret", False),
        ],
        command=["--run-id", run.id, "--prompt", prompt, "--expires", "2000000000", "--collector"],
        docker=docker,
    )
    container.start()
    monkeypatch.setattr(codex_runtime.time, "time", lambda: request["deadline"] + 1)
    docker.fail = "rm"
    assert runtime.inspect(run.id).status == "unknown"
    assert (folder / "settlement.json").exists()
    reopened = CodexMockRuntime(tmp_path, docker=docker)
    docker.available = False
    assert reopened.inspect(run.id).status == "unknown"
    docker.available = True
    result = reopened.inspect(run.id)
    assert result.status == "failed" and result.cost == 0
    assert not docker.states
    assert [call[0] for call in docker.calls].count("create") == 1
    assert [call[0] for call in docker.calls].count("start") == 1


def test_changed_assets_after_runtime_construction_prevent_container_dispatch(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "hearth.db")
    database.initialize(runtime_kind="codex_mock")
    hearth = Hearth(database, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    fixture = tmp_path / "synthetic-asset"
    fixture.write_text("original")

    def verify_assets(*args):
        if fixture.read_text() != "original":
            raise Refused("codex_mock_assets_changed")
        return "a" * 64

    monkeypatch.setattr(codex_assets, "prepare", verify_assets)
    monkeypatch.setattr(codex_runtime.threading.Thread, "start", lambda self: None)
    docker = Docker()
    runtime = CodexMockRuntime(tmp_path, docker=docker)
    task = hearth.submit("request", "reader", "Synthetic summary", expires_at=1_788_640_100)
    run = hearth.admit(task.task_id, reserve=1000)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    execution.prepare_start(run.id, run.owner_token)
    with database.transaction() as db:
        context = read_context(db, run.id, Memory(hearth).files)
    runtime.start(run.id, json.dumps(context, sort_keys=True, separators=(",", ":")))
    fixture.write_text("changed after server startup")
    runtime._worker(run.id)
    assert runtime.inspect(run.id).status == "unknown"
    assert not any(call[0] in {"create", "start"} for call in docker.calls)
