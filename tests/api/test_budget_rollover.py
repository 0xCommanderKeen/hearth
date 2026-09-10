"""Time alone refreshes the operator projection, never the audit log."""

import asyncio
import json
from datetime import datetime
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.residents.models import Declaration

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader_via

from .test_api import AUTH, TOKEN


def instant(value):
    return int(datetime.fromisoformat(value + "+00:00").timestamp())


def query(state):
    return {key: state[key] for key in ("cursor", "epoch", "budget_revision")}


@pytest.mark.parametrize(
    "start,boundary,household_rolls",
    [
        ("2026-09-06T21:59:59", "2026-09-06T22:00:00", True),
        ("2026-09-06T23:59:59", "2026-09-07T00:00:00", False),
        ("2026-03-28T23:00:00", "2026-03-29T22:00:00", True),
        ("2026-10-24T22:00:00", "2026-10-25T23:00:00", True),
    ],
)
def test_conditional_and_connected_stream_refresh_without_audit(
    tmp_path, start, boundary, household_rolls
):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    now = [instant(start)]
    app.state.hearth.clock = lambda: now[0]
    with TestClient(app) as client:
        seed_reader_via(client)
        app.state.hearth.save_resident(
            "utc", Declaration("UTC", "Synthetic", 1_000_000, "UTC"), expected_revision=0
        )
        receipt = app.state.hearth.submit("spent", "reader", "Synthetic", expires_at=now[0] + 100)
        app.state.hearth.admit(receipt.task_id, reserve=2000)
        app.state.executor.step()
        first = client.get("/api/state", headers=AUTH).json()
        assert first["household"]["spent"] > 0
        assert client.get("/api/state", params=query(first), headers=AUTH).status_code == 204
        # Legacy clients without projection identity must receive a safe full refresh.
        assert (
            client.get(
                "/api/state",
                params={"cursor": first["cursor"], "epoch": first["epoch"]},
                headers=AUTH,
            ).status_code
            == 200
        )

        async def stream():
            incoming = asyncio.Queue()
            await incoming.put({"type": "http.request", "body": b"", "more_body": False})
            frames = []

            async def send(message):
                if message["type"] == "http.response.start":
                    assert message["status"] == 200
                if message["type"] == "http.response.body" and message.get("body"):
                    frame = message["body"].decode()
                    frames.append(frame)
                    if frame.startswith(": keepalive"):
                        now[0] = instant(boundary)
                    elif frame.startswith("event:"):
                        await incoming.put({"type": "http.disconnect"})

            await asyncio.wait_for(
                app(
                    {
                        "type": "http",
                        "asgi": {"version": "3.0"},
                        "http_version": "1.1",
                        "method": "GET",
                        "scheme": "http",
                        "path": "/api/events",
                        "raw_path": b"/api/events",
                        "query_string": urlencode(query(first)).encode(),
                        "headers": [(b"authorization", AUTH["Authorization"].encode())],
                        "client": ("127.0.0.1", 1234),
                        "server": ("test", 80),
                    },
                    incoming.get,
                    send,
                ),
                timeout=5,
            )
            assert frames[0] == ": keepalive\n\n"
            assert frames[1].startswith("event: snapshot\ndata: ")
            return json.loads(frames[1].split("data: ", 1)[1])

        streamed = asyncio.run(stream())
        response = client.get("/api/state", params=query(first), headers=AUTH)
        assert response.status_code == 200
        updated = response.json()
        assert streamed == updated
        assert updated["cursor"] == first["cursor"]
        assert updated["epoch"] == first["epoch"]
        assert updated["activity"] == first["activity"]
        assert updated["budget_revision"] != first["budget_revision"]
        if household_rolls:
            assert updated["household"]["budget_day"] != first["household"]["budget_day"]
            assert updated["household"]["spent"] == 0
        else:
            assert updated["household"] == first["household"]
        assert client.get("/api/state", params=query(updated), headers=AUTH).status_code == 204


def test_pinned_household_window_expires_after_timezone_edit(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())
    now = [instant("2026-09-06T12:00:00")]
    app.state.hearth.clock = lambda: now[0]
    with TestClient(app) as client:
        seed_reader_via(client)
        receipt = app.state.hearth.submit("spent", "reader", "Synthetic", expires_at=now[0] + 100)
        app.state.hearth.admit(receipt.task_id, reserve=2000)
        app.state.executor.step()
        # Both current calendars remain on the same day at 22:00 UTC. Only the
        # original pinned Ljubljana window ends; Tokyo's current day began at 15:00.
        reader = app.state.hearth.resident("reader")
        app.state.hearth.save_resident(
            "reader",
            Declaration("Reader", "Synthetic", 10_000_000, "UTC"),
            expected_revision=reader.revision,
        )
        now[0] = instant("2026-09-06T21:59:59")
        response = client.put(
            "/api/household",
            headers=AUTH,
            json={
                "daily_limit": 10_000_000,
                "timezone": "Asia/Tokyo",
                "resident_limit": 20,
                "concurrency_limit": 2,
                "expected_revision": 0,
            },
        )
        assert response.status_code == 200
        before = client.get("/api/state", headers=AUTH).json()
        assert before["household"]["spent"] > 0
        now[0] += 1
        response = client.get("/api/state", params=query(before), headers=AUTH)
        assert response.status_code == 200
        after = response.json()
        assert after["cursor"] == before["cursor"]
        assert after["household"]["budget_day"] == before["household"]["budget_day"]
        assert after["household"]["spent"] == 0
