"""Accepted long instructions remain observable without repeating their full content."""

import json

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader

from .test_api import AUTH, TOKEN


@pytest.mark.parametrize("unit", ["a", "🦔漢\n"])
def test_large_task_snapshot_keeps_full_instruction_reachable(tmp_path, unit):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    seed_reader(hearth)
    instruction = (unit * 32_000)[:32_000]
    receipts = [
        hearth.submit(f"large-{i}", "reader", instruction, expires_at=int(hearth.clock()) + 3600)
        for i in range(100)
    ]
    with TestClient(app) as client:
        state = client.get("/api/state", headers=AUTH).json()
        assert len(state["tasks"]) == 100
        assert len(json.dumps(state)) < 500_000
        for task in state["tasks"]:
            assert task["instruction"] == instruction[:240]
            assert task["instruction_truncated"] is True
        task_id = receipts[0].task_id
        response = client.get(f"/api/tasks/{task_id}", headers=AUTH)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["instruction"] == instruction
        assert client.get(f"/api/tasks/{task_id}").status_code == 401
        assert client.get("/api/tasks/missing", headers=AUTH).status_code == 404
        # Detail remains accessible when the snapshot's recent-task window moves on.
        hearth.submit("new", "reader", "Short", expires_at=int(hearth.clock()) + 3600)
        assert (
            client.get(f"/api/tasks/{task_id}", headers=AUTH).json()["instruction"] == instruction
        )
