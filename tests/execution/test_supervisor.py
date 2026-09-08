"""Process-lifetime ownership and real thread shutdown, with bounded test barriers."""

import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.residents.models import Refused

from tests.fake_runtime import fake_runtime


@pytest.fixture
def app(tmp_path):
    app = create_app(tmp_path / "data", "synthetic-test-token", runtime=fake_runtime())
    yield app
    app.state.supervisor.stop()


def test_second_api_supervisor_is_rejected_until_first_shuts_down(app, tmp_path):
    second = create_app(tmp_path / "data", "synthetic-test-token", runtime=fake_runtime())
    with TestClient(app) as client:
        with pytest.raises(Refused, match="supervisor_busy"), TestClient(second):
            pass
        response = client.get(
            "/api/health", headers={"Authorization": "Bearer synthetic-test-token"}
        )
        assert response.json()["supervisor"] == "running"
    with TestClient(second):
        assert second.state.supervisor.health()["supervisor"] == "running"
    assert second.state.supervisor.health()["supervisor"] == "stopped"


def test_canonical_directory_alias_cannot_start_second_supervisor(app, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "data", target_is_directory=True)
    second = create_app(alias, "synthetic-test-token", runtime=fake_runtime())
    app.state.supervisor.start()
    with pytest.raises(Refused, match="supervisor_busy"):
        second.state.supervisor.start()


def test_shutdown_retains_ownership_until_blocked_operation_finishes(app, tmp_path, monkeypatch):
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    original = app.state.executor.step

    def blocked():
        entered.set()
        assert release.wait(5), "test must release its blocked mock operation"
        return original()

    monkeypatch.setattr(app.state.executor, "step", blocked)
    worker = app.state.supervisor
    second = create_app(
        tmp_path / "data", "synthetic-test-token", runtime=fake_runtime()
    ).state.supervisor
    worker.start()
    stopper = None
    try:
        assert entered.wait(3)
        stopper = threading.Thread(target=lambda: (worker.stop(), stopped.set()))
        stopper.start()
        assert not stopped.wait(0.05)
        assert worker.health()["supervisor"] == "stopping"
        with pytest.raises(Refused, match="supervisor_busy"):
            second.start()
        release.set()
        assert stopped.wait(3)
        second.start()
        assert second.health()["supervisor"] == "running"
    finally:
        release.set()
        if stopper:
            stopper.join(3)
        second.stop()


def test_shutdown_does_not_start_next_stage_after_current_one_drains(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    admitted = []
    worker = app.state.supervisor

    def blocked_tick():
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(worker.routines, "tick", blocked_tick)
    monkeypatch.setattr(worker.routines, "admit_queued", lambda: admitted.append(True))
    worker.start()
    stopper = None
    try:
        assert entered.wait(3)
        stopper = threading.Thread(target=worker.stop)
        stopper.start()
        # stop() sets its visible state before waiting for the active tick.
        for _ in range(100):
            if worker.health()["supervisor"] == "stopping":
                break
            threading.Event().wait(0.005)
        assert worker.health()["supervisor"] == "stopping"
        release.set()
        stopper.join(3)
        assert not stopper.is_alive()
        assert admitted == []
    finally:
        release.set()
        if stopper:
            stopper.join(3)


def test_foreign_process_cannot_acquire_supervision(app, tmp_path):
    app.state.supervisor.start()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from hearth.app import create_app
from hearth.residents.models import Refused
from tests.fake_runtime import fake_runtime
supervisor = create_app(
    Path(sys.argv[1]), "synthetic-test-token", runtime=fake_runtime()
).state.supervisor
try:
    supervisor.start()
    print("started")
except Refused as error:
    print(error.code)
finally:
    supervisor.stop()
""",
            str(tmp_path / "data"),
            str(Path(__file__).parents[2]),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "supervisor_busy"


def test_worker_failure_is_visible_and_releases_ownership(app, monkeypatch):
    worker = app.state.supervisor
    failed = threading.Event()

    def fatal():
        failed.set()
        raise SystemExit("private failure text")

    monkeypatch.setattr(worker.routines, "tick", fatal)
    worker.start()
    assert failed.wait(3)
    worker.stop()
    state = worker.health()
    assert state["supervisor"] == "failed"
    assert state["executor_error"] == "SystemExit"
    assert "private" not in str(state)


@pytest.mark.parametrize(
    ("allowance", "capacity", "refusal"),
    [(100_000, 1, "household_concurrency_limit"), (10_000, 2, "household_budget_exhausted")],
)
def test_blocked_routine_does_not_stall_an_admitted_run(tmp_path, allowance, capacity, refusal):
    import time
    from datetime import datetime

    from hearth.authority.household import Household
    from hearth.execution.lifecycle import Execution, Executor
    from hearth.execution.supervisor import Supervisor
    from hearth.observation.notifications import MockInbox, Notifications
    from hearth.residents.models import Declaration
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.database import Database
    from hearth.work.routines import Routines
    from hearth.work.service import Hearth

    from tests.fake_runtime import FakeRuntime

    database = Database(tmp_path / "hearth.db")
    database.initialize()
    now = [int(datetime.fromisoformat("2026-09-06T09:00:00+00:00").timestamp())]
    hearth = Hearth(database, clock=lambda: now[0])
    Household(hearth).save(
        daily_limit=allowance,
        timezone="UTC",
        resident_limit=2,
        concurrency_limit=capacity,
        expected_revision=0,
    )
    for name in ("active", "scheduled"):
        hearth.save_resident(name, Declaration(name, "Synthetic", 100_000), expected_revision=0)
    task = hearth.submit("first", "active", "Summarize", expires_at=now[0] + 600)
    active = hearth.admit(task.task_id, reserve=10_000)
    routines = Routines(hearth)
    routines.save(
        "daily",
        "scheduled",
        "Summarize",
        local_time="09:01",
        timezone="UTC",
        enabled=True,
        expected_revision=0,
    )
    now[0] += 60
    queued = routines.tick()[0]
    with pytest.raises(Refused, match=refusal):
        hearth.admit(queued, reserve=10_000)
    worker = Supervisor(
        Executor(Execution(hearth, Artifacts(tmp_path / "artifacts")), FakeRuntime(tmp_path)),
        routines,
        Notifications(hearth, MockInbox(tmp_path / "inbox")),
    )
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while hearth.run(active.id).status != "succeeded" and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        assert hearth.run(active.id).status == "succeeded"
        if capacity == 1:
            while hearth.task(queued).status != "succeeded" and time.monotonic() < deadline:
                threading.Event().wait(0.02)
            assert hearth.task(queued).status == "succeeded"
        else:
            assert hearth.task(queued).status == "queued"
        assert worker.health()["executor_error"] is None
    finally:
        worker.stop()
