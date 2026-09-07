"""Management's native receipt settles only the matching admitted grant and run."""

import hashlib
import json
from dataclasses import asdict

import pytest
from hearth.execution.lifecycle import Execution
from hearth.execution.usage import binding, details
from hearth.integrations.codex.subscription import KIND
from hearth.integrations.interface import encode_receipt
from hearth.management.bootstrap import bootstrap
from hearth.management.bridge import BoundRun, Bridge
from hearth.observation.snapshot import snapshot
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth


def native_terminal():
    return {
        "protocol": "codex-app-server-0.153.4",
        "launched": True,
        "cancelled": False,
        "error": None,
        "exit_code": -15,
        "catalog_sha256": "b" * 64,
        "tools_sha256": "c" * 64,
        "events": [
            {
                "method": "thread/started",
                "params": {"thread": {"id": "thread", "model": "gpt-6-astra"}},
            },
            {"method": "turn/started", "params": {"threadId": "thread", "turn": {"id": "turn"}}},
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread",
                    "turnId": "turn",
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 30,
                            "inputTokens": 20,
                            "outputTokens": 10,
                            "cachedInputTokens": 0,
                            "reasoningOutputTokens": 0,
                        }
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {
                        "id": "turn",
                        "status": "completed",
                        "error": None,
                        "items": [
                            {
                                "type": "agentMessage",
                                "text": "Synthetic manager result",
                                "phase": "final_answer",
                            }
                        ],
                    },
                },
            },
        ],
    }


def manager_run(tmp_path):
    db = Database(tmp_path / "data/hearth.db")
    db.initialize(runtime_kind=KIND)
    hearth = Hearth(db)
    karen = bootstrap(hearth)
    with db.transaction(write=True) as connection:
        connection.execute("INSERT INTO system_meta VALUES ('codex_live_binary',?)", ("a" * 64,))
    task = hearth.submit(
        "manager", karen["resident_id"], "Create a reporter", expires_at=int(hearth.clock()) + 600
    )
    run = hearth.admit(task.task_id, reserve=100000)
    execution = Execution(hearth, Artifacts(tmp_path / "data/artifacts"))
    execution.prepare_start(run.id, run.owner_token)
    bridge = Bridge(
        hearth, BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    )
    bridge.bind_thread("thread")
    bridge.bind_turn("thread", "turn")
    with db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE run_management SET catalog_sha256=?,tools_sha256=? WHERE run_id=?",
            ("b" * 64, "c" * 64, run.id),
        )
        row = connection.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
        bound = binding(connection, row)
    receipt = {
        "kind": KIND,
        "protocol": "management",
        "binding": asdict(bound),
        "binary": "a" * 64,
        "terminal": native_terminal(),
    }
    return hearth, run, execution, bound, receipt


def test_native_management_usage_result_details_and_held_backup(tmp_path):
    from hearth.storage.backup import capture, restore

    hearth, run, execution, bound, receipt = manager_run(tmp_path)
    raw, digest, evidence = encode_receipt(receipt, bound)
    assert digest == hashlib.sha256(raw.encode()).hexdigest()
    assert evidence.cost == 700 and evidence.output == "Synthetic manager result"
    finished = execution.finish(run.id, run.owner_token, evidence, _usage_receipt=receipt)
    assert finished.actual_cost == 700
    with hearth.database.transaction() as db:
        assert details(db, run.id)["requests"] == []
    capture(tmp_path / "data", tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    assert Hearth(Database(tmp_path / "held/hearth.db")).run(run.id).actual_cost == 700
    assert not any("auth" in str(path) for path in (tmp_path / "backup").rglob("*"))


def test_backup_rejects_changed_admission_grant_and_held_copy_refuses_bridge(tmp_path):
    from hearth.management.bridge import BoundRun, Bridge
    from hearth.residents.models import Refused
    from hearth.storage.backup import capture, restore

    hearth, run, execution, bound, receipt = manager_run(tmp_path)
    execution.finish(
        run.id, run.owner_token, encode_receipt(receipt, bound)[2], _usage_receipt=receipt
    )
    capture(tmp_path / "data", tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    held = Hearth(Database(tmp_path / "held/hearth.db"))
    bridge = Bridge(
        held, BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    )
    result = bridge.call(
        dict(threadId="thread", turnId="turn", callId="held", tool="hearth_catalog", arguments={})
    )
    assert not result["success"] and "restored_copy_read_only" in str(result)
    with hearth.database.transaction(write=True) as db:
        db.execute("UPDATE run_management SET grant_sha256=? WHERE run_id=?", ("0" * 64, run.id))
    with pytest.raises(Refused, match="management_"):
        capture(tmp_path / "data", tmp_path / "corrupt")


def test_subscription_worker_routes_granted_run_through_actual_structured_callback(
    tmp_path, monkeypatch
):
    from hearth.execution.context import read_context
    from hearth.integrations.codex import app_server
    from hearth.integrations.codex.subscription import VERSION, CodexLiveRuntime, worker
    from hearth.residents.memory import Memory

    data = tmp_path / "data"
    db = Database(data / "hearth.db")
    db.initialize(runtime_kind=KIND)
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "auth.json").write_text("synthetic-only")
    binary = tmp_path / "codex"
    binary.write_text("synthetic native executable")
    monkeypatch.setattr(
        "hearth.integrations.codex.subscription.subprocess.check_output", lambda *a, **kw: VERSION
    )
    monkeypatch.setattr(
        app_server,
        "configuration_pins",
        lambda *a, **kw: {"catalog_sha256": "b" * 64, "tools_sha256": "c" * 64},
        raising=False,
    )
    runtime = CodexLiveRuntime(data, binary=binary, auth_home=auth)
    hearth = Hearth(db)
    karen = bootstrap(hearth)
    task = hearth.submit(
        "management",
        karen["resident_id"],
        "Create a reporter",
        expires_at=int(hearth.clock()) + 600,
    )
    run = hearth.admit(task.task_id, reserve=100000)
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    execution.prepare_start(run.id, run.owner_token)
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
    calls = []

    def native(**kwargs):
        assert run.owner_token not in kwargs["prompt"] and str(auth) not in kwargs["prompt"]
        assert kwargs["max_calls"] == 64 and 0 < kwargs["timeout"] <= 600
        kwargs["on_thread"]("thread")
        with kwargs["dispatch_guard"]():
            pass
        kwargs["on_turn"]("thread", "turn")
        params = dict(
            threadId="thread",
            turnId="turn",
            callId="native-creation",
            namespace=None,
            tool="hearth_residents_provision",
            arguments={
                "operation_id": "native-creation",
                "resident": {
                    "name": "Native-created reporter",
                    "purpose": "Read synthetic notes",
                    "execution_profile": KIND,
                    "creation_reason": "Actual structured native callback",
                    "daily_limit": 100000,
                    "first_assignment": {"instruction": "Report on fictional notes"},
                },
            },
        )
        result = kwargs["on_tool"](params)
        assert result["success"]
        calls.append(json.loads(result["contentItems"][0]["text"]))
        terminal = native_terminal()
        terminal["events"].insert(2, {"id": 0, "method": "item/tool/call", "params": params})
        return terminal

    monkeypatch.setattr(app_server, "run", native, raising=False)
    worker(runtime.folder(run.id))
    evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
    assert evidence.status == "succeeded" and evidence.cost == 700
    assert len(calls) == 1 and calls[0]["task_id"]
    child = hearth.resident(calls[0]["resident_id"])
    assert child.declaration.name == "Native-created reporter"
    worker(runtime.folder(run.id))
    assert len(calls) == 1
    assert runtime.receipt(run.id)["protocol"] == "management"


@pytest.mark.parametrize("damage", ["catalog", "tools", "thread", "turn"])
def test_foreign_native_configuration_and_session_cannot_settle(tmp_path, damage):
    from hearth.residents.models import Refused

    hearth, run, execution, bound, receipt = manager_run(tmp_path)
    terminal = receipt["terminal"]
    if damage in {"catalog", "tools"}:
        terminal[damage + "_sha256"] = "d" * 64
    else:
        for event in terminal["events"]:
            params = event["params"]
            if damage + "Id" in params:
                params[damage + "Id"] = "foreign"
            if damage in params:
                params[damage]["id"] = "foreign"
    evidence = encode_receipt(receipt, bound)[2]
    with pytest.raises(Refused, match="management_"):
        execution.finish(run.id, run.owner_token, evidence, _usage_receipt=receipt)
    assert hearth.run(run.id).finished_at is None


def test_missing_native_usage_keeps_hold_and_survives_current_data_backup(tmp_path):
    from hearth.storage.backup import capture

    hearth, run, execution, bound, receipt = manager_run(tmp_path)
    del receipt["terminal"]["events"][2]
    evidence = encode_receipt(receipt, bound)[2]
    assert evidence.cost is None
    finished = execution.finish(run.id, run.owner_token, evidence, _usage_receipt=receipt)
    assert not finished.usage_known
    state = snapshot(hearth)
    assert state["household"]["unknown"] == 100000
    assert state["residents"][0]["pause_reason"] == "usage_unknown"
    capture(tmp_path / "data", tmp_path / "backup")
