import hashlib
import json
from dataclasses import asdict

import pytest
from hearth.integrations.codex.pricing import MODEL
from hearth.integrations.codex.subscription import KIND, encode
from hearth.integrations.codex.usage import UsageBinding
from hearth.residents.models import Refused

BINDING = UsageBinding("real-run", hashlib.sha256(b"notes").hexdigest(), MODEL, "standard")


def receipt(*, total=10194, written=0):
    usage = {
        "input_tokens": total,
        "cached_input_tokens": 6912,
        "output_tokens": 75,
        "reasoning_output_tokens": 0,
    }
    if written is not None:
        usage["cache_write_input_tokens"] = written
    events = [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "id": "answer", "text": "A real-model summary."},
        },
        {"type": "turn.completed", "usage": usage},
    ]
    return {
        "kind": KIND,
        "binding": asdict(BINDING),
        "binary": "a" * 64,
        "stdout": "\n".join(json.dumps(e) for e in events),
        "final": "A real-model summary.",
        "exit_code": 0,
        "cancelled": False,
        "launched": True,
    }


def test_real_receipt_preserves_output_and_prices_complete_short_context_counters():
    _, _, result = encode(receipt(), BINDING)
    assert result.status == "succeeded" and result.cost == 43482
    assert result.output == "A real-model summary."
    assert "Simulation" not in result.output


@pytest.mark.parametrize("value", [receipt(total=272001), receipt(written=None)])
def test_uncertain_pricing_preserves_summary_but_leaves_usage_unknown(value):
    result = encode(value, BINDING)[2]
    assert result.status == "succeeded" and result.cost is None


def test_cancel_before_launch_has_zero_usage_but_cannot_hide_output():
    value = receipt() | {
        "stdout": "",
        "final": None,
        "exit_code": None,
        "cancelled": True,
        "launched": False,
    }
    result = encode(value, BINDING)[2]
    assert result.status == "cancelled" and result.cost == 0
    value["stdout"] = receipt()["stdout"]
    with pytest.raises(Refused):
        encode(value, BINDING)


def test_real_receipt_cannot_settle_another_input():
    value = receipt()
    value["binding"]["input_digest"] = "b" * 64
    with pytest.raises(Refused):
        encode(value, BINDING)


def test_cli_setup_diagnostic_before_turn_does_not_discard_success():
    value = receipt()
    lines = value["stdout"].splitlines()
    diagnostic = {
        "type": "item.completed",
        "item": {
            "id": "setup",
            "type": "error",
            "message": (
                "Code Mode is unavailable because code-mode host is disabled. "
                "Code mode will fail closed; enable `features.code_mode_host` and "
                "install `codex-code-mode-host`."
            ),
        },
    }
    lines.insert(1, json.dumps(diagnostic))
    value["stdout"] = "\n".join(lines)
    assert encode(value, BINDING)[2].status == "succeeded"


def test_live_result_and_usage_survive_backup_without_auth_files(tmp_path, monkeypatch):
    from hearth.execution.lifecycle import Execution
    from hearth.execution.usage import binding
    from hearth.integrations.codex.subscription import VERSION, CodexLiveRuntime
    from hearth.residents.models import Declaration
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.backup import capture, restore, verify
    from hearth.storage.database import Database
    from hearth.work.service import Hearth

    data = tmp_path / "data"
    auth = tmp_path / "private-login"
    auth.mkdir()
    (auth / "auth.json").write_text("synthetic-test-only")
    binary = tmp_path / "codex"
    binary.write_text("synthetic-executable-test-only")
    monkeypatch.setattr(
        "hearth.integrations.codex.subscription.subprocess.check_output",
        lambda *args, **kwargs: VERSION,
    )
    db = Database(data / "hearth.db")
    db.initialize()
    CodexLiveRuntime(data, binary=binary, auth_home=auth)
    hearth = Hearth(db, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    task = hearth.submit("live", "reader", "Summarize", expires_at=1_788_640_100)
    run = hearth.admit(task.task_id, reserve=100_000)
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    execution.prepare_start(run.id, run.owner_token)
    with db.transaction() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
        bound = binding(connection, row)
    value = receipt() | {
        "binding": asdict(bound),
        "binary": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
    result = execution.finish(
        run.id, run.owner_token, encode(value, bound)[2], _usage_receipt=value
    )
    metadata, content = execution.artifact(result.artifact_id)
    assert content == "A real-model summary."
    assert result.actual_cost == 43482
    capture(data, tmp_path / "backup")
    manifest = verify(tmp_path / "backup")
    assert not any("auth" in name for name in manifest["files"])
    restore(tmp_path / "backup", tmp_path / "held")
    held = Database(tmp_path / "held/hearth.db")
    assert held.restored()
    assert Hearth(held).run(run.id).actual_cost == 43482


@pytest.mark.parametrize(
    "tail", ['{"type":', "[1]", '{"type":"item.completed","item":null}', '{"message":"�']
)
@pytest.mark.parametrize("cancelled", [True, False])
def test_truncated_transcript_keeps_observed_termination_with_unknown_usage(tail, cancelled):
    value = receipt() | {
        "stdout": '{"type":"thread.started","thread_id":"test"}\n' + tail,
        "exit_code": -15,
        "final": None,
        "cancelled": cancelled,
    }
    evidence = encode(value, BINDING)[2]
    assert evidence.status == ("cancelled" if cancelled else "failed")
    assert evidence.cost is None


def prepared_worker(tmp_path, monkeypatch):
    import sys

    from hearth.execution.context import read_context
    from hearth.execution.lifecycle import Execution
    from hearth.integrations.codex.subscription import CodexLiveRuntime
    from hearth.residents.memory import Memory
    from hearth.residents.models import Declaration
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.database import Database
    from hearth.work.service import Hearth

    data = tmp_path / "data"
    auth = tmp_path / "login"
    auth.mkdir()
    (auth / "auth.json").write_text("synthetic-only")
    binary = tmp_path / "codex"
    binary.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.153.4')\n sys.exit()\n"
        'sys.stdout.buffer.write(b\'{"type":"\\xe2\')\n'
        "sys.stdout.buffer.flush()\ntime.sleep(30)\n"
    )
    binary.chmod(0o700)
    db = Database(data / "hearth.db")
    db.initialize()
    runtime = CodexLiveRuntime(data, binary=binary, auth_home=auth)
    hearth = Hearth(db)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 10_000_000), expected_revision=0
    )
    task = hearth.submit("worker", "reader", "界" * 32000, expires_at=int(hearth.clock()) + 100)
    run = hearth.admit(task.task_id, reserve=100_000)
    Execution(hearth, Artifacts(data / "artifacts")).prepare_start(run.id, run.owner_token)
    with db.transaction() as connection:
        prompt = json.dumps(
            read_context(connection, run.id, Memory(hearth).files),
            sort_keys=True,
            separators=(",", ":"),
        )
    with monkeypatch.context() as patch:
        patch.setattr(
            "hearth.integrations.codex.subscription.subprocess.Popen", lambda *a, **kw: None
        )
        runtime.start(run.id, prompt)
    return runtime, run


def test_worker_timeout_covers_child_that_never_reads_large_prompt(tmp_path, monkeypatch):
    import time

    from hearth.integrations.codex.subscription import worker

    runtime, run = prepared_worker(tmp_path, monkeypatch)
    monkeypatch.setattr("hearth.integrations.codex.subscription.RUN_TIMEOUT", 0.2)
    started = time.monotonic()
    worker(runtime.folder(run.id))
    assert time.monotonic() - started < 5
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "failed" and evidence.cost is None
    assert runtime.receipt(run.id)["exit_code"] is not None


@pytest.mark.parametrize("field", ["prompt", "sha256"])
def test_worker_refuses_changed_persisted_launch_input(tmp_path, monkeypatch, field):
    from hearth.integrations.codex.subscription import worker

    runtime, run = prepared_worker(tmp_path, monkeypatch)
    path = runtime.folder(run.id) / "request.json"
    value = json.loads(path.read_text())
    value[field] = "changed"
    path.write_text(json.dumps(value))

    def unexpected_launch(*a, **kw):
        pytest.fail("Changed input reached process launch")

    monkeypatch.setattr(
        "hearth.integrations.codex.subscription.subprocess.Popen", unexpected_launch
    )
    # The session itself is started through the launcher, whichever one this run was
    # admitted under, so that is the launch the refusal has to happen before.
    monkeypatch.setattr("hearth.integrations.launcher.subprocess.Popen", unexpected_launch)
    worker(runtime.folder(run.id))
    assert runtime.inspect(run.id).status == "unknown"


def test_worker_cancellation_retains_partial_utf8_termination_receipt(tmp_path, monkeypatch):
    import threading

    from hearth.integrations.codex.subscription import worker

    runtime, run = prepared_worker(tmp_path, monkeypatch)
    monkeypatch.setattr("hearth.integrations.codex.subscription.RUN_TIMEOUT", 3)
    timer = threading.Timer(0.2, runtime.stop, args=(run.id,))
    timer.start()
    try:
        worker(runtime.folder(run.id))
    finally:
        timer.join()
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "cancelled" and evidence.cost is None
    assert runtime.receipt(run.id)["exit_code"] is not None
