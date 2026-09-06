"""Two independent mock control planes share one execution fence, not their work DBs."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.accounting import Accounting
from hearth.artifacts import Artifacts
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.ownership import ExecutionGuard, Ownership
from hearth.runtime import MockRuntime

NOW = 1_788_640_000


@pytest.fixture
def systems(tmp_path):
    registry = Ownership(tmp_path / "shared/ownership.db")
    registry.initialize()
    result = []
    for name in ("source", "target"):
        root = tmp_path / name
        db = Database(root / "hearth.db")
        db.initialize()
        hearth = Hearth(db, clock=lambda: NOW)
        hearth.save_resident(
            "reader", Declaration("Reader", "Synthetic", 10000), expected_revision=0
        )
        guard = ExecutionGuard(registry, name, hearth)
        executor = Executor(
            Execution(hearth, Artifacts(root / "artifacts")),
            MockRuntime(root / "runtime"),
            guard=guard,
        )
        result.append((hearth, executor, guard))
    registry.register("reader", "source")
    return registry, *result


def admit(hearth, command):
    task = hearth.submit(command, "reader", "Synthetic", expires_at=NOW + 600)
    return hearth.admit(task.task_id, reserve=3000)


def transfer(registry, command="handoff", source="source", target="target", revision=1):
    return registry.transfer(
        command, "reader", source=source, target=target, expected_revision=revision
    )


def test_active_claim_survives_restart_and_blocks_handoff(systems):
    registry, (source, executor, _), _ = systems
    run = admit(source, "work")
    executor.runtime.scenario = "hold"
    executor.step()
    reopened = Ownership(registry.path)
    reopened.initialize()
    assert reopened.state("reader")["claimed_run"] == run.id
    with pytest.raises(Refused, match="execution_claim_unsettled"):
        transfer(reopened)
    executor.execution.cancel(run.id)
    executor.step()
    assert source.run(run.id).status == "cancelled"
    assert reopened.state("reader")["claimed_run"] is None
    assert transfer(reopened)["revision"] == 2


def test_target_runs_after_transfer_and_old_owner_cannot_restart(systems):
    registry, (source, old, _), (target, new, _) = systems
    first = admit(source, "first")
    old.step()
    receipt = transfer(registry)
    assert receipt == transfer(registry)
    second = admit(target, "second")
    new.step()
    assert target.run(second.id).status == "succeeded"
    stale = admit(source, "stale")
    old.step()
    assert source.run(stale.id).status == "interrupted"
    assert not source.run(stale.id).launch_attempted
    assert old.runtime.inspect(stale.id).status == "absent"
    assert source.run(first.id).status == "succeeded"
    returned = transfer(registry, "return", source="target", target="source", revision=2)
    assert returned["revision"] == 3
    assert transfer(registry) == receipt  # Replay is the original receipt, not current state.
    assert registry.state("reader")["system_id"] == "source"
    old.step()
    assert old.runtime.inspect(stale.id).status == "absent"  # No blind replay after handback.


def test_unknown_usage_prevents_transfer_until_explicit_settlement(systems):
    registry, (source, executor, guard), _ = systems
    run = admit(source, "unknown-cost")
    executor.runtime.scenario = "unknown_usage"
    executor.step()
    assert source.run(run.id).finished_at is not None
    with pytest.raises(Refused, match="execution_claim_unsettled"):
        transfer(registry)
    Accounting(source).reconcile("usage", run.id, amount=2000, evidence="Synthetic report")
    guard.settle()
    assert transfer(registry)["revision"] == 2


def test_missing_runtime_evidence_never_releases_claim(systems):
    registry, (source, executor, _), _ = systems
    run = admit(source, "uncertain")
    executor.runtime.scenario = "hold"
    executor.step()
    (executor.runtime.root / (run.id + ".json")).unlink()
    executor.step()
    assert source.run(run.id).status == "interrupted"
    with pytest.raises(Refused, match="execution_claim_unsettled"):
        transfer(registry)


def test_crash_after_terminal_commit_keeps_claim_until_reconciliation(systems, monkeypatch):
    registry, (source, executor, guard), _ = systems
    run = admit(source, "work")
    original = guard.settle
    calls = 0

    def crash():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic crash after settlement")
        original()

    with monkeypatch.context() as patch:
        patch.setattr(guard, "settle", crash)
        with pytest.raises(OSError):
            executor.step()
    assert source.run(run.id).status == "succeeded"
    with pytest.raises(Refused, match="execution_claim_unsettled"):
        transfer(registry)
    executor.step()
    assert registry.state("reader")["claimed_run"] is None
    assert transfer(registry)["revision"] == 2


def test_claim_and_transfer_race_have_one_winner(systems):
    registry, (source, executor, _), _ = systems
    run = admit(source, "race")
    executor.runtime.scenario = "hold"

    def handoff():
        try:
            return transfer(registry)
        except Refused as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        launch = pool.submit(executor.step)
        handover = pool.submit(handoff)
        launch.result()
        result = handover.result()
    if isinstance(result, dict):
        assert executor.runtime.inspect(run.id).status == "absent"
        assert source.run(run.id).status == "interrupted"
    else:
        assert result == "execution_claim_unsettled"
        assert executor.runtime.inspect(run.id).status == "running"
        assert registry.state("reader")["system_id"] == "source"


def test_identity_binding_and_conflicting_commands_are_refused(systems, tmp_path):
    registry, (source, _, _), _ = systems
    with pytest.raises(Refused, match="ownership_system_binding_changed"):
        registry.bind("source", tmp_path / "different.db")
    with pytest.raises(Refused, match="ownership_database_already_bound"):
        registry.bind("imposter", source.database.path)
    transfer(registry)
    with pytest.raises(Refused, match="ownership_command_conflict"):
        transfer(registry, source="target", target="source", revision=2)
    with pytest.raises(Refused, match="execution_owner_changed"):
        registry.register("reader", "source")


def test_transfer_audit_failure_rolls_back_owner_and_receipt(systems):
    registry, _, _ = systems
    with sqlite3.connect(registry.path) as db:
        db.execute("""CREATE TRIGGER fail BEFORE INSERT ON events WHEN NEW.kind='transferred'
            BEGIN SELECT RAISE(ABORT,'synthetic registry disk failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        transfer(registry)
    assert registry.state("reader")["revision"] == 1
    with sqlite3.connect(registry.path) as db:
        assert db.execute("SELECT count(*) FROM transfers").fetchone()[0] == 0


def test_restored_source_cannot_bind_or_release_execution(systems):
    registry, (source, _, _), _ = systems
    with source.database.transaction(write=True) as db:
        db.execute(
            "INSERT INTO system_meta VALUES ('restore_hold', ?)", (json.dumps({"synthetic": True}),)
        )
    with pytest.raises(Refused, match="restored_copy_read_only"):
        ExecutionGuard(registry, "source", source)


def test_missing_registry_fails_closed(systems):
    registry, (source, worker, _), _ = systems
    run = admit(source, "missing-registry")
    registry.path.unlink()
    with pytest.raises(Refused, match="ownership_not_initialized"):
        worker.step()
    assert worker.runtime.inspect(run.id).status == "absent"


def test_observable_rehearsal_uses_fresh_synthetic_systems(tmp_path):
    from hearth.rehearsal import execution_handoff

    result = execution_handoff(tmp_path / "rehearsal")
    assert result["active_transfer_refused"]
    assert result["source_run"] == "cancelled"
    assert result["target_run"] == "succeeded"
    assert result["stale_source_run"] == "interrupted"
    assert result["owner"]["system_id"] == "target"
    with pytest.raises(Refused, match="rehearsal_destination_exists"):
        execution_handoff(tmp_path / "rehearsal")


@pytest.mark.parametrize("claim_first", [True, False])
def test_both_concurrent_transfer_orderings_are_forced(systems, monkeypatch, claim_first):
    from threading import Event

    registry, (source, executor, guard), _ = systems
    run = admit(source, "ordered-race")
    executor.runtime.scenario = "hold"
    reached, proceed = Event(), Event()
    original = guard.claim

    def paused_claim(run):
        if claim_first:
            original(run)
        reached.set()
        assert proceed.wait(5)
        if not claim_first:
            original(run)

    monkeypatch.setattr(guard, "claim", paused_claim)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.step)
        try:
            assert reached.wait(5)
            if claim_first:
                with pytest.raises(Refused, match="execution_claim_unsettled"):
                    transfer(registry)
            else:
                assert transfer(registry)["revision"] == 2
        finally:
            proceed.set()
        future.result()
    assert executor.runtime.inspect(run.id).status == ("running" if claim_first else "absent")
    assert registry.state("reader")["system_id"] == ("source" if claim_first else "target")


def test_denied_resident_does_not_starve_authorized_work(systems):
    registry, (source, executor, _), _ = systems
    denied = admit(source, "denied")
    transfer(registry)
    source.clock = lambda: NOW + 1
    source.save_resident("other", Declaration("Other", "Synthetic", 10000), expected_revision=0)
    registry.register("other", "source")
    task = source.submit("allowed", "other", "Synthetic", expires_at=NOW + 600)
    allowed = source.admit(task.task_id, reserve=3000)
    executor.step()
    assert source.run(denied.id).status == "interrupted"
    assert executor.runtime.inspect(denied.id).status == "absent"
    assert source.run(allowed.id).status == "succeeded"
