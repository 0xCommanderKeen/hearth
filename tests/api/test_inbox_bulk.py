"""Bulk read applies to the observed inbox, including rows outside its visible window."""

from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.observation.notifications import record

from tests.fake_runtime import fake_runtime

from .test_api import AUTH, TOKEN


def test_bulk_read_requires_auth_and_keeps_later_arrivals_unread(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    with hearth.database.transaction(write=True) as db:
        for index in range(125):
            record(db, "run.succeeded", f"run-{index}", int(hearth.clock()))
    with TestClient(app) as client:
        snapshot = client.get("/api/state", headers=AUTH).json()
        assert snapshot["unread_notifications"] == 125
        assert len(snapshot["notifications"]) == 100
        url = f"/api/notifications/read-all?through_cursor={snapshot['cursor']}"
        assert client.post(url).status_code == 401
        assert client.post("/api/notifications/read-all", headers=AUTH).status_code == 422
        assert (
            client.post("/api/notifications/read-all?through_cursor=-1", headers=AUTH).status_code
            == 422
        )
        with hearth.database.transaction(write=True) as db:
            record(db, "run.succeeded", "later-run", int(hearth.clock()))
        response = client.post(url, headers=AUTH)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"marked_read": 125}
        assert client.post(url, headers=AUTH).json() == {"marked_read": 0}
        assert client.get("/api/state", headers=AUTH).json()["unread_notifications"] == 1
