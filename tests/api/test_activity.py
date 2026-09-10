"""Activity belongs to a resident and remains reachable beyond recent snapshots."""

from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.residents.models import Declaration

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader

from .test_api import AUTH, TOKEN


def test_activity_is_authenticated_scoped_and_keyset_paged(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    seed_reader(hearth)
    hearth.save_resident("other", Declaration("Other", "Synthetic", 10000), expected_revision=0)
    first = hearth.submit(
        "first", "reader", "Private task text", expires_at=int(hearth.clock()) + 3600
    )
    for index in range(40):
        hearth.submit(
            f"other-{index}", "other", "Other private text", expires_at=int(hearth.clock()) + 3600
        )
    second = hearth.submit(
        "second", "reader", "Another task", expires_at=int(hearth.clock()) + 3600
    )
    with TestClient(app) as client:
        url = "/api/residents/reader/activity"
        assert client.get(url).status_code == 401
        response = client.get(url + "?limit=1", headers=AUTH)
        assert response.headers["cache-control"] == "no-store"
        page = response.json()
        assert page["entries"][0]["resource_id"] == second.task_id
        hearth.submit("new", "reader", "New arrival", expires_at=int(hearth.clock()) + 3600)
        older = client.get(url + f"?limit=100&before={page['next_before']}", headers=AUTH)
        entries = older.json()["entries"]
        assert any(row["resource_id"] == first.task_id for row in entries)
        assert all(row["sequence"] < page["next_before"] for row in entries)
        assert all(row["resource_id"] not in {second.task_id, "other"} for row in entries)
        assert "Other private text" not in older.text
        assert "Private task text" not in older.text
        assert "owner_token" not in older.text
        assert "detail" not in older.text
        assert client.get("/api/residents/missing/activity", headers=AUTH).status_code == 404
        assert client.get(url + "?limit=101", headers=AUTH).status_code == 422
        assert client.get(url + "?before=0", headers=AUTH).status_code == 422


def test_activity_links_run_events_and_explains_interruption(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth, execution = app.state.hearth, app.state.execution
    seed_reader(hearth)
    task = hearth.submit("one", "reader", "Synthetic", expires_at=int(hearth.clock()) + 3600)
    run = hearth.admit(task.task_id, reserve=10000)
    execution.observe(run.id, run.owner_token, "interrupted")
    with TestClient(app) as client:
        page = client.get("/api/residents/reader/activity", headers=AUTH).json()
        event = next(row for row in page["entries"] if row["kind"] == "run.interrupted")
        assert event["run_id"] == run.id
        assert event["run_status"] == "interrupted"
        assert event["diagnostic"] is None
