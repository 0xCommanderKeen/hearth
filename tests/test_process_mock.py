"""Real POSIX processes and SQLite; all model output and usage are synthetic."""

import hashlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.artifacts import Artifacts
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.process_mock import ProcessMockRuntime, worker, write_json


def eventually(read, accept, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if accept(result):
            return result
        time.sleep(0.02)
    raise AssertionError(f"Timed out: {result!r}")


def terminal(runtime, run_id):
    return eventually(
        lambda: runtime.inspect(run_id),
        lambda result: result.status in {"succeeded", "failed", "cancelled"},
    )


def test_concurrent_start_is_one_child_and_receipt_survives_restart(tmp_path):
    runtime = ProcessMockRuntime(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: runtime.start("run", "same input"), range(8)))
    result = terminal(runtime, "run")
    assert result.status == "succeeded" and result.cost == 2_000
    assert "No model was called" in result.output
    folder = runtime.folder("run")
    before = (folder / "result.json").read_bytes()
    reopened = ProcessMockRuntime(tmp_path)
    reopened.start("run", "same input")
    worker(folder)  # Even a directly repeated worker cannot launch a second child.
    assert reopened.inspect("run") == result
    assert (folder / "result.json").read_bytes() == before
    with pytest.raises(Refused, match="runtime_identity_conflict"):
        reopened.start("run", "different input")


def test_lost_launch_acknowledgement_never_retries(tmp_path, monkeypatch):
    runtime = ProcessMockRuntime(tmp_path)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError("injected ambiguous spawn")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(OSError):
        runtime.start("run", "input")
    assert runtime.inspect("run").status == "unknown"
    runtime.start("run", "input")
    assert calls == [1]
    assert not (runtime.folder("run") / "child-started").exists()


def test_pending_cancellation_is_observed_before_child_launch(tmp_path):
    runtime = ProcessMockRuntime(tmp_path)
    folder = runtime.folder("run")
    folder.mkdir()
    write_json(
        folder / "request.json",
        {
            "simulated": True,
            "boundary": "posix",
            "instruction_digest": hashlib.sha256(b"input").hexdigest(),
            "scenario": "hold",
            "timeout": 2,
        },
    )
    runtime.stop("run")
    worker(folder)
    assert runtime.inspect("run").status == "cancelled"
    assert runtime.inspect("run").cost == 0
    assert not (folder / "child-started").exists()


def test_cancellation_stops_child_and_descendant_after_adapter_restart(tmp_path):
    runtime = ProcessMockRuntime(tmp_path, scenario="hold")
    runtime.start("run", "input")
    heartbeat = runtime.folder("run") / "heartbeat"
    try:
        eventually(heartbeat.exists, bool)
        assert runtime.inspect("run").status == "running"
        reopened = ProcessMockRuntime(tmp_path)
        reopened.stop("run")
        result = terminal(reopened, "run")
        assert result.status == "cancelled" and result.cost == 1_000
        last = heartbeat.read_bytes()
        time.sleep(0.15)
        assert heartbeat.read_bytes() == last
    finally:
        runtime.stop("run")
        terminal(runtime, "run")


def test_worker_loss_is_unknown_and_does_not_spawn_again(tmp_path, monkeypatch):
    original = subprocess.Popen
    workers = []

    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        workers.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture)
    runtime = ProcessMockRuntime(tmp_path, scenario="hold", timeout=1)
    runtime.start("run", "input")
    heartbeat = runtime.folder("run") / "heartbeat"
    try:
        eventually(heartbeat.exists, bool)
        workers[0].kill()
        workers[0].wait(timeout=3)
        assert runtime.inspect("run").status == "unknown"
        runtime.start("run", "input")
        assert len(workers) == 1
        assert not (runtime.folder("run") / "result.json").exists()
    finally:
        if workers[0].poll() is None:
            workers[0].kill()
            workers[0].wait(timeout=3)
        # Orphan fixtures self-expire. This does not claim arbitrary model
        # descendants are terminated when the trusted worker itself is killed.
        time.sleep(1.3)


def test_deadline_has_unknown_usage_and_corrupt_receipt_is_not_success(tmp_path):
    runtime = ProcessMockRuntime(tmp_path, scenario="hold", timeout=0.3)
    runtime.start("run", "input")
    result = terminal(runtime, "run")
    assert result.status == "failed" and result.cost is None
    (runtime.folder("run") / "result.json").write_text("invalid")
    assert runtime.inspect("run").status == "unknown"


def test_engine_restart_recovers_process_result_in_sqlite(tmp_path):
    db = Database(tmp_path / "hearth.db")
    db.initialize(runtime_kind="process_mock")
    hearth = Hearth(db)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    command = hearth.submit(
        "command", "reader", "Synthetic notes", expires_at=int(time.time()) + 300
    )
    run = hearth.admit(command.task_id, reserve=5_000)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    runtime = ProcessMockRuntime(tmp_path / "runtime")
    Executor(execution, runtime).step()
    terminal(runtime, run.id)
    reopened = Hearth(Database(tmp_path / "hearth.db"))
    resumed = Execution(reopened, Artifacts(tmp_path / "artifacts"))
    Executor(resumed, ProcessMockRuntime(tmp_path / "runtime")).step()
    done = reopened.run(run.id)
    assert done.status == "succeeded" and done.actual_cost == 2_000
    assert resumed.artifact(done.artifact_id)[0].simulated
    assert len([fact for fact in reopened.audit() if fact["kind"] == "run.succeeded"]) == 1


def test_parent_process_exit_does_not_stop_detached_worker(tmp_path):
    code = (
        "from pathlib import Path; from hearth.process_mock import ProcessMockRuntime; "
        f"ProcessMockRuntime(Path({str(tmp_path)!r}),scenario='hold').start('run','input')"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True, timeout=5)
    runtime = ProcessMockRuntime(tmp_path)
    try:
        eventually(lambda: (tmp_path / "run/heartbeat").exists(), bool)
        assert runtime.inspect("run").status == "running"
        runtime.stop("run")
        assert terminal(runtime, "run").status == "cancelled"
    finally:
        runtime.stop("run")
        terminal(runtime, "run")


def test_lost_ack_after_actual_spawn_recovers_without_relaunch(tmp_path, monkeypatch):
    original = subprocess.Popen
    children = []

    def lose_ack(*args, **kwargs):
        children.append(original(*args, **kwargs))
        raise OSError("lost spawn acknowledgement")

    monkeypatch.setattr(subprocess, "Popen", lose_ack)
    runtime = ProcessMockRuntime(tmp_path)
    try:
        with pytest.raises(OSError):
            runtime.start("run", "input")
        runtime.start("run", "input")
        assert terminal(runtime, "run").status == "succeeded"
        assert len(children) == 1
    finally:
        runtime.stop("run")
        for child in children:
            child.wait(timeout=5)


@pytest.mark.parametrize(
    "scenario,status,cost",
    [
        ("failure", "failed", 1_000),
        ("unknown_usage", "succeeded", None),
    ],
)
def test_child_fixture_preserves_terminal_usage(scenario, status, cost, tmp_path):
    runtime = ProcessMockRuntime(tmp_path, scenario=scenario)
    runtime.start("run", "input")
    result = terminal(runtime, "run")
    assert result.status == status and result.cost == cost


def test_ambiguous_process_launch_keeps_sqlite_ownership_on_cancel(tmp_path, monkeypatch):
    database = Database(tmp_path / "hearth.db")
    database.initialize(runtime_kind="process_mock")
    hearth = Hearth(database)
    hearth.save_resident("reader", Declaration("Reader", "Synthetic", 10_000), expected_revision=0)
    receipt = hearth.submit("first", "reader", "Synthetic", expires_at=int(time.time()) + 300)
    run = hearth.admit(receipt.task_id, reserve=5_000)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    runtime = ProcessMockRuntime(tmp_path / "runtime")
    executor = Executor(execution, runtime)

    def fail(*args, **kwargs):
        raise OSError("ambiguous spawn")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(OSError):
        executor.step()
    execution.cancel(run.id)
    executor.step()
    interrupted = hearth.run(run.id)
    assert interrupted.status == "interrupted"
    assert interrupted.finished_at is None and interrupted.cancellation_requested
    assert interrupted.launch_attempted and interrupted.reserved == 5_000
    second = hearth.submit("second", "reader", "Synthetic", expires_at=int(time.time()) + 300)
    with pytest.raises(Refused, match="resident_busy"):
        hearth.admit(second.task_id, reserve=5_000)
