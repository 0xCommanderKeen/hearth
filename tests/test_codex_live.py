import hashlib
import json
from dataclasses import asdict

import pytest
from hearth.codex_live import KIND, encode
from hearth.codex_pricing import MODEL
from hearth.codex_usage import UsageBinding
from hearth.models import Refused

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
    from hearth.artifacts import Artifacts
    from hearth.backup import capture, restore, verify
    from hearth.codex_accounting import binding
    from hearth.codex_live import VERSION, CodexLiveRuntime
    from hearth.core import Hearth
    from hearth.database import Database
    from hearth.execution import Execution
    from hearth.models import Declaration

    data = tmp_path / "data"
    auth = tmp_path / "private-login"
    auth.mkdir()
    (auth / "auth.json").write_text("synthetic-test-only")
    binary = tmp_path / "codex"
    binary.write_text("synthetic-executable-test-only")
    monkeypatch.setattr(
        "hearth.codex_live.subprocess.check_output", lambda *args, **kwargs: VERSION
    )
    db = Database(data / "hearth.db")
    db.initialize(runtime_kind=KIND)
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
    assert metadata.simulated is False and content == "A real-model summary."
    assert result.actual_cost == 43482
    capture(data, tmp_path / "backup")
    manifest = verify(tmp_path / "backup")
    assert manifest["simulated"] is False
    assert not any("auth" in name for name in manifest["files"])
    restore(tmp_path / "backup", tmp_path / "held")
    held = Database(tmp_path / "held/hearth.db")
    assert held.restored()
    assert Hearth(held).run(run.id).actual_cost == 43482
