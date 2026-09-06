"""Portable evidence preserves synthetic operational state without transferring authority."""

import hashlib
import json
import sqlite3

import pytest
from hearth.artifacts import Artifacts
from hearth.authority import Authority
from hearth.backup import capture
from hearth.broker import Broker, MockNoticeboard
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.portable import export, validate
from hearth.run_access import RunAccess
from hearth.runtime import MockRuntime


@pytest.fixture
def system(tmp_path):
    root = tmp_path / "original"
    database = Database(root / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader",
        Declaration("Reader", "Synthetic", 1_000_000, "Europe/Ljubljana"),
        expected_revision=0,
    )
    task = hearth.submit("summary", "reader", "Synthetic", expires_at=1_788_640_600)
    run = hearth.admit(task.task_id, reserve=10_000)
    credential = RunAccess(hearth).issue(run.id, run.owner_token)
    executor = Executor(
        Execution(hearth, Artifacts(root / "artifacts")), MockRuntime(root / "mock-runtime")
    )
    return hearth, executor, run, credential, root


def produce(system, tmp_path):
    *_, root = system
    source = tmp_path / "backup"
    capture(root, source)
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    result = export(source, tmp_path / "export")
    assert before == {
        str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    raw = (tmp_path / "export/state.json").read_bytes()
    assert validate(raw) == result
    return json.loads(raw), result, raw


def test_completed_state_preserves_values_and_file_bytes_without_credentials(system, tmp_path):
    hearth, executor, run, credential, root = system
    executor.step()
    doc, result, raw = produce(system, tmp_path)
    assert result["rows"]["runs"] == 1
    assert result["rows"]["artifacts"] == 1
    assert run.owner_token.encode() not in raw and credential.token.encode() not in raw
    assert "run_credentials" not in doc["tables"] and "system_meta" not in doc["tables"]
    with hearth.database.transaction() as db:
        for name, rows in doc["tables"].items():
            original = [dict(row) for row in db.execute(f'SELECT * FROM "{name}"')]
            if name == "runs":
                for row in original:
                    row.pop("owner_token")
            assert {json.dumps(row, sort_keys=True) for row in original} == {
                json.dumps(row, sort_keys=True) for row in rows
            }
        digest = db.execute("SELECT digest FROM run_credentials").fetchone()[0]
        assert digest.encode() not in raw
    for name, value in doc["files"].items():
        assert value["text"].encode() == (root / name).read_bytes()
    assert doc["tables"]["runs"][0]["budget_timezone"] == "Europe/Ljubljana"
    assert not hearth.database.restored()
    assert (tmp_path / "export").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "export/state.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("scenario", ["hold", "unknown_usage"])
def test_pending_cancellation_and_unknown_usage_remain_evidence(system, tmp_path, scenario):
    hearth, executor, run, _, _ = system
    executor.runtime.scenario = scenario
    executor.step()
    if scenario == "hold":
        executor.execution.cancel(run.id)
    hearth.set_paused("reader", paused=True, expected_revision=0)
    doc, _, _ = produce(system, tmp_path)
    row = doc["tables"]["runs"][0]
    assert row["status"] == hearth.run(run.id).status
    assert row["usage_known"] == 0 and row["actual_cost"] is None
    assert doc["tables"]["operator_controls"][0]["paused"] == 1
    if scenario == "unknown_usage":
        assert doc["tables"]["pauses"][0]["reason"] == "usage_unknown"
    else:
        assert row["cancellation_requested"] == 1
        assert executor.runtime.inspect(run.id).status == "running"


def test_uncertain_publication_receipt_is_preserved_without_replay(system, tmp_path, monkeypatch):
    hearth, executor, run, _, root = system
    executor.step()
    authority = Authority(hearth, executor.execution.artifacts)
    authority.set_publication_policy("reader", enabled=True, expected_revision=0)
    proposal = authority.request("review", run.id, expires_at=1_788_640_600)
    authority.decide(proposal.id, reviewed_digest=proposal.digest, approve=True)
    effect = MockNoticeboard(root / "mock-noticeboard")
    original = effect.publish

    def lost_ack(*args):
        original(*args)
        raise OSError("synthetic lost acknowledgement")

    monkeypatch.setattr(effect, "publish", lost_ack)
    broker = Broker(authority, effect)
    assert broker.execute(proposal.id)["status"] == "unknown"
    doc, _, _ = produce(system, tmp_path)
    assert doc["tables"]["publication_actions"][0]["status"] == "unknown"
    assert doc["tables"]["approvals"][0]["status"] == "approved"
    assert any(name.startswith("mock-noticeboard/") for name in doc["files"])
    assert broker.inspect(proposal.id)["status"] == "unknown"
    from hearth.portable import import_state

    import_state((tmp_path / "export/state.json").read_bytes(), tmp_path / "imported")
    copy = Hearth(Database(tmp_path / "imported/hearth.db"), clock=hearth.clock)
    copy_effect = MockNoticeboard(tmp_path / "imported/mock-noticeboard")
    copy_broker = Broker(Authority(copy, Artifacts(tmp_path / "imported/artifacts")), copy_effect)
    assert copy_effect.inspect(proposal.id).digest == proposal.digest
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy_broker.execute(proposal.id)
    assert copy_broker.inspect(proposal.id)["status"] == "unknown"


def test_digest_ignores_order_and_epoch_but_detects_operational_changes(system, tmp_path):
    system[1].step()
    doc, original, _ = produce(system, tmp_path)
    doc["source_epoch"] = "different-observation-epoch"
    for rows in doc["tables"].values():
        rows.reverse()
    assert validate(json.dumps(doc).encode())["semantic_sha256"] == original["semantic_sha256"]
    doc["tables"]["declarations"][0]["daily_limit"] += 1
    assert validate(json.dumps(doc).encode())["semantic_sha256"] != original["semantic_sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_field",
        "extra_table",
        "missing_table",
        "extra_column",
        "missing_column",
        "bool_amount",
        "float_amount",
        "null_primary",
        "bad_reference",
        "bad_current_revision",
        "bad_artifact_reference",
        "duplicate_row",
        "bad_status",
        "future_schema",
        "bool_version",
        "non_mock",
        "path",
        "file_hash",
        "missing_artifact",
        "sequence",
        "duplicate_sequence",
    ],
)
def test_malformed_export_is_refused(system, tmp_path, mutation):
    system[1].step()
    doc, _, _ = produce(system, tmp_path)
    tables = doc["tables"]
    if mutation == "extra_field":
        doc["activate"] = True
    elif mutation == "extra_table":
        tables['evil"; DROP TABLE runs; --'] = []
    elif mutation == "missing_table":
        tables.pop("pauses")
    elif mutation == "extra_column":
        tables["runs"][0]["owner_token"] = "must-not-be-accepted"
    elif mutation == "missing_column":
        tables["runs"][0].pop("reserved")
    elif mutation == "bool_amount":
        tables["runs"][0]["actual_cost"] = True
    elif mutation == "float_amount":
        tables["runs"][0]["actual_cost"] = 2.0
    elif mutation == "null_primary":
        tables["residents"][0]["id"] = None
    elif mutation == "bad_reference":
        tables["runs"][0]["task_id"] = "absent"
    elif mutation == "bad_current_revision":
        tables["residents"][0]["revision"] = 999
    elif mutation == "bad_artifact_reference":
        tables["runs"][0]["artifact_id"] = "absent"
    elif mutation == "duplicate_row":
        tables["runs"].append(tables["runs"][0])
    elif mutation == "bad_status":
        tables["runs"][0]["status"] = "invented"
    elif mutation == "future_schema":
        doc["schema"] += 1
    elif mutation == "bool_version":
        doc["version"] = True
    elif mutation == "non_mock":
        doc["simulated"] = False
    elif mutation == "path":
        doc["files"]["../outside"] = {"text": "", "sha256": hashlib.sha256(b"").hexdigest()}
    elif mutation == "file_hash":
        next(iter(doc["files"].values()))["text"] += "changed"
    elif mutation == "missing_artifact":
        doc["files"] = {}
    elif mutation == "sequence":
        tables["sqlite_sequence"][0]["seq"] = 0
    else:
        tables["sqlite_sequence"] *= 2
    with pytest.raises(Refused):
        validate(json.dumps(doc).encode())


@pytest.mark.parametrize(
    "raw", [b"{}", b'{"version":1,"version":1}', b"null", b"[]", b"broken", b"\xff"]
)
def test_invalid_envelope_is_refused(raw):
    with pytest.raises(Refused):
        validate(raw)


@pytest.mark.parametrize("change", ["table", "column", "metadata"])
def test_unknown_source_state_is_not_silently_dropped(system, tmp_path, change):
    *_, root = system
    with sqlite3.connect(root / "hearth.db") as db:
        if change == "table":
            db.execute("CREATE TABLE future_state (id TEXT)")
        elif change == "column":
            db.execute("ALTER TABLE residents ADD COLUMN future_state TEXT")
        else:
            db.execute("INSERT INTO system_meta VALUES ('future_authority', 'must-preserve')")
    capture(root, tmp_path / "backup")
    with pytest.raises(Refused, match="portable_(schema|metadata)_unsupported"):
        export(tmp_path / "backup", tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_existing_destination_and_corrupted_backup_are_refused(system, tmp_path):
    system[1].step()
    produce(system, tmp_path)
    with pytest.raises(Refused, match="backup_destination_exists"):
        export(tmp_path / "backup", tmp_path / "export")
    (tmp_path / "backup/hearth.db").write_bytes(b"corrupted")
    with pytest.raises(Refused):
        export(tmp_path / "backup", tmp_path / "never-published")
    assert not (tmp_path / "never-published").exists()


def test_routines_usage_reports_and_notification_receipts_are_carried(system, tmp_path):
    from hearth.accounting import Accounting
    from hearth.notifications import MockInbox, Notifications
    from hearth.routines import Routines

    hearth, executor, run, _, root = system
    executor.runtime.scenario = "unknown_usage"
    executor.step()
    Accounting(hearth).reconcile("usage-report", run.id, amount=2345, evidence="Synthetic report")
    routines = Routines(hearth)
    routines.save(
        "daily",
        resident_id="reader",
        instruction="Synthetic routine",
        local_time="09:00",
        timezone="Europe/Ljubljana",
        enabled=True,
        expected_revision=0,
    )
    hearth.clock = lambda: 1_788_740_000
    assert routines.tick()
    Notifications(hearth, MockInbox(root / "mock-inbox")).step()
    doc, _, _ = produce(system, tmp_path)
    assert doc["tables"]["usage_reconciliations"][0]["amount"] == 2345
    assert doc["tables"]["runs"][0]["actual_cost"] == 2345
    assert len(doc["tables"]["occurrences"]) == 1
    assert doc["tables"]["routines"][0]["id"] == "daily"
    assert any(name.startswith("mock-inbox/") for name in doc["files"])


def test_high_water_mark_survives_pruned_audit(system, tmp_path):
    hearth = system[0]
    with hearth.database.transaction(write=True) as db:
        db.execute("DELETE FROM audit")
    doc, _, _ = produce(system, tmp_path)
    assert not doc["tables"]["audit"]
    assert doc["tables"]["sqlite_sequence"][0]["seq"] > 0


def test_input_and_row_limits_are_enforced(system, tmp_path, monkeypatch):
    import hearth.portable as portable

    _, _, raw = produce(system, tmp_path)
    monkeypatch.setattr(portable, "MAX_EXPORT", len(raw) - 1)
    with pytest.raises(Refused, match="portable_too_large"):
        validate(raw)
    monkeypatch.setattr(portable, "MAX_EXPORT", len(raw))
    monkeypatch.setattr(portable, "MAX_ROWS", 1)
    with pytest.raises(Refused, match="portable_too_large"):
        validate(raw)


def test_empty_database_can_be_exported(tmp_path):
    Database(tmp_path / "empty/hearth.db").initialize()
    capture(tmp_path / "empty", tmp_path / "backup")
    result = export(tmp_path / "backup", tmp_path / "export")
    assert result["rows"]["runs"] == 0 and result["files"] == 0


@pytest.mark.parametrize("scenario", ["success", "hold", "unknown_usage"])
def test_import_roundtrip_and_retry_preserve_state_under_a_read_only_hold(
    system, tmp_path, scenario
):
    from hearth.portable import import_state

    hearth, executor, run, _, _ = system
    executor.runtime.scenario = scenario
    executor.step()
    if scenario == "hold":
        executor.execution.cancel(run.id)
    hearth.set_paused("reader", paused=True, expected_revision=0)
    _, expected, raw = produce(system, tmp_path)
    target = tmp_path / "imported"
    result = import_state(raw, target)
    assert result == import_state(raw, target)
    assert result["semantic_sha256"] == expected["semantic_sha256"]
    copy = Hearth(Database(target / "hearth.db"), clock=hearth.clock)
    assert copy.database.restored()
    assert copy.run(run.id).owner_token != run.owner_token
    assert copy.run(run.id).status == hearth.run(run.id).status
    assert copy.receipt("summary") == hearth.receipt("summary")
    with copy.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM run_credentials").fetchone()[0] == 0
    with pytest.raises(Refused, match="restored_copy_read_only"):
        Executor(
            Execution(copy, Artifacts(target / "artifacts")), MockRuntime(target / "mock-runtime")
        ).step()
    capture(target, tmp_path / "reverse-backup")
    reverse = export(tmp_path / "reverse-backup", tmp_path / "reverse")
    assert reverse["semantic_sha256"] == expected["semantic_sha256"]
    second = import_state((tmp_path / "reverse/state.json").read_bytes(), tmp_path / "second")
    assert second["epoch"] != result["epoch"]
    assert second["semantic_sha256"] == result["semantic_sha256"]


def test_imported_api_remains_read_only_and_old_runtime_token_is_denied(system, tmp_path):
    from fastapi.testclient import TestClient
    from hearth.api import create_app
    from hearth.portable import import_state

    _, _, run, credential, _ = system
    _, _, raw = produce(system, tmp_path)
    import_state(raw, tmp_path / "imported")
    token = "synthetic-operator-token"
    with TestClient(create_app(tmp_path / "imported", token, supervise=True)) as client:
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/api/state", headers=headers).json()["restore_hold"] is True
        response = client.post(
            "/api/residents/reader/pause",
            headers=headers,
            json={"paused": True, "expected_revision": 0},
        )
        assert response.status_code == 409
        response = client.get(
            f"/api/runtime/runs/{run.id}/context",
            headers={"Authorization": "Bearer " + credential.token},
        )
        assert response.status_code == 401


@pytest.mark.parametrize("change", ["input", "row", "file", "hold", "symlink"])
def test_import_retry_never_overwrites_different_or_changed_state(system, tmp_path, change):
    from hearth.portable import import_state

    system[1].step()
    doc, _, raw = produce(system, tmp_path)
    target = tmp_path / "imported"
    import_state(raw, target)
    if change == "input":
        doc["tables"]["declarations"][0]["daily_limit"] += 1
        raw = json.dumps(doc).encode()
    elif change == "row":
        with sqlite3.connect(target / "hearth.db") as db:
            db.execute("UPDATE declarations SET daily_limit=daily_limit+1")
    elif change == "file":
        next((target / "artifacts").iterdir()).write_text("changed")
    elif change == "hold":
        with sqlite3.connect(target / "hearth.db") as db:
            db.execute("DELETE FROM system_meta WHERE key='restore_hold'")
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(target, target_is_directory=True)
        target = alias
    before = (target / "hearth.db").read_bytes()
    with pytest.raises(Refused):
        import_state(raw, target)
    assert (target / "hearth.db").read_bytes() == before


def test_concurrent_imports_converge_on_one_copy(system, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from hearth.portable import import_state

    _, _, raw = produce(system, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_state(raw, tmp_path / "imported"), range(2)))
    assert results[0] == results[1]


def test_failed_import_does_not_publish_partial_destination(system, tmp_path, monkeypatch):
    import hearth.backup as backup
    from hearth.portable import import_state

    system[1].step()
    _, _, raw = produce(system, tmp_path)

    def disk_full(*args):
        raise OSError("synthetic disk full")

    with monkeypatch.context() as patch:
        patch.setattr(backup, "_write", disk_full)
        with pytest.raises(OSError, match="disk full"):
            import_state(raw, tmp_path / "imported")
    assert not (tmp_path / "imported").exists()
    assert not list(tmp_path.glob(".hearth-copy-*"))
    assert import_state(raw, tmp_path / "imported")["read_only"] is True
