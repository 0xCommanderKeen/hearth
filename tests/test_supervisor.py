"""Process-lifetime ownership and real thread shutdown, with bounded test barriers."""

import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from hearth.api import create_app
from hearth.models import Refused


@pytest.fixture
def app(tmp_path):
    app = create_app(tmp_path / "data", "synthetic-test-token")
    yield app
    app.state.supervisor.stop()


def test_second_api_supervisor_is_rejected_until_first_shuts_down(app, tmp_path):
    second = create_app(tmp_path / "data", "synthetic-test-token")
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
    second = create_app(alias, "synthetic-test-token")
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
    second = create_app(tmp_path / "data", "synthetic-test-token").state.supervisor
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
from hearth.api import create_app
from hearth.models import Refused
supervisor = create_app(Path(sys.argv[1]), "synthetic-test-token").state.supervisor
try:
    supervisor.start()
    print("started")
except Refused as error:
    print(error.code)
finally:
    supervisor.stop()
""",
            str(tmp_path / "data"),
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
