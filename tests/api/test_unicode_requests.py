"""Character limits survive both JSON encodings at the authenticated HTTP boundary."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from hearth.api.auth import MAX_DECLARATION_BODY, MAX_ROUTINE_BODY, MAX_TASK_BODY
from hearth.app import create_app

from tests.fake_runtime import fake_runtime
from tests.support import seed_reader_via

from .test_api import AUTH, TOKEN

ROUTES = {
    "task": ("POST", "/api/tasks", MAX_TASK_BODY),
    "routine": ("POST", "/api/routines/unicode", MAX_ROUTINE_BODY),
    "declaration": ("PUT", "/api/residents/reader", MAX_DECLARATION_BODY),
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, supervise=False, runtime=fake_runtime())) as client:
        seed_reader_via(client)
        yield client


def payload(route, text):
    if route == "declaration":
        return {
            "name": text[:100],
            "purpose": text[:8000],
            "skill_text": text,
            "daily_limit": 10_000_000,
            "budget_timezone": "UTC",
            "expected_revision": 1,
        }
    body = {"resident_id": "reader", "instruction": text}
    if route == "task":
        return {**body, "expires_at": int(time.time()) + 3600}
    return {
        **body,
        "local_time": "09:00",
        "timezone": "UTC",
        "enabled": False,
        "expected_revision": 0,
    }


def send(client, route, content):
    method, path, _ = ROUTES[route]
    return client.request(
        method,
        path,
        headers={**AUTH, "Idempotency-Key": "unicode", "Content-Type": "application/json"},
        content=content,
    )


@pytest.mark.parametrize("route", ["task", "routine", "declaration"])
@pytest.mark.parametrize("unit", ["漢", "🦔"])
@pytest.mark.parametrize("escaped", [False, True])
def test_maximum_unicode_roundtrips(client, route, unit, escaped):
    text = unit * 32_000
    body = payload(route, text)
    response = send(client, route, json.dumps(body, ensure_ascii=escaped).encode("utf-8"))
    assert response.status_code == (201 if route == "task" else 200), response.text
    if route == "task":
        saved = client.get("/api/tasks/" + response.json()["task_id"], headers=AUTH).json()
        assert saved["instruction"] == text
    elif route == "routine":
        saved = client.get("/api/state", headers=AUTH).json()["routines"][0]
        assert saved["instruction"] == text
    else:
        saved = client.get("/api/residents/reader", headers=AUTH).json()["declaration"]
        assert all(saved[key] == body[key] for key in ("name", "purpose", "skill_text"))


@pytest.mark.parametrize("route", ["task", "routine", "declaration"])
def test_semantically_oversized_unicode_is_refused_without_mutation(client, route):
    before = client.get("/api/state", headers=AUTH).json()
    response = send(client, route, json.dumps(payload(route, "🦔" * 32_001)).encode())
    assert response.status_code == (409 if route == "declaration" else 422)
    after = client.get("/api/state", headers=AUTH).json()
    assert after["cursor"] == before["cursor"]
    assert after["tasks"] == after["routines"] == []
    assert client.get("/api/residents/reader", headers=AUTH).json()["revision"] == 1


@pytest.mark.parametrize("route", ["task", "routine", "declaration"])
def test_transport_budget_accepts_boundary_and_refuses_extra_byte(client, route):
    from hearth.api.auth import MAX_DECLARATION_BODY, MAX_ROUTINE_BODY, MAX_TASK_BODY

    limit = {
        "task": MAX_TASK_BODY,
        "routine": MAX_ROUTINE_BODY,
        "declaration": MAX_DECLARATION_BODY,
    }[route]
    body = json.dumps(payload(route, "漢")).encode().ljust(limit, b" ")
    before = client.get("/api/state", headers=AUTH).json()["cursor"]
    response = send(client, route, body + b" ")
    assert response.status_code == 413
    assert response.json() == {"error": "body_too_large"}
    assert client.get("/api/state", headers=AUTH).json()["cursor"] == before
    assert send(client, route, body).status_code == (201 if route == "task" else 200)


@pytest.mark.parametrize("route", ["task", "routine", "declaration"])
def test_chunked_transport_stops_at_budget_without_reading_remainder(client, route):
    import asyncio

    from hearth.api.auth import MAX_DECLARATION_BODY, MAX_ROUTINE_BODY, MAX_TASK_BODY

    limit = {
        "task": MAX_TASK_BODY,
        "routine": MAX_ROUTINE_BODY,
        "declaration": MAX_DECLARATION_BODY,
    }[route]
    method, path = {
        "task": ("POST", "/api/tasks"),
        "routine": ("POST", "/api/routines/unicode"),
        "declaration": ("PUT", "/api/residents/reader"),
    }[route]
    reads = 0
    messages = []

    async def receive():
        nonlocal reads
        reads += 1
        assert reads <= 2, "transport read past its budget"
        return {
            "type": "http.request",
            "body": b" " * (limit if reads == 1 else 1),
            "more_body": True,
        }

    async def capture(message):
        messages.append(message)

    before = client.get("/api/state", headers=AUTH).json()["cursor"]
    asyncio.run(
        client.app(
            {
                "type": "http",
                "method": method,
                "path": path,
                "headers": [(b"authorization", AUTH["Authorization"].encode())],
            },
            receive,
            capture,
        )
    )
    assert reads == 2
    assert messages[0]["status"] == 413
    assert client.get("/api/state", headers=AUTH).json()["cursor"] == before
