"""Authenticated bounded projections and actions through real temporary SQLite."""

import json

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.channels.chat.model import Connection, Grant
from hearth.channels.worker import Worker
from hearth.residents.models import Refused
from hearth.storage.backup import capture, restore

from tests.channels.test_conversations import house as house_fixture
from tests.channels.test_conversations import message
from tests.channels.test_delivery import notice, receipt, setup
from tests.fake_runtime import fake_runtime
from tests.work.test_letter_replies import bridge_of, settle

house = house_fixture
AUTH = {"Authorization": "Bearer synthetic-operator-token"}
ROOT = "/api/communications"


def client_of(house, factories=None):
    app = create_app(
        house[1].database.path.parent,
        "synthetic-operator-token",
        supervise=False,
        runtime=fake_runtime(),
        communications=lambda hearth: Worker(hearth, house[8], factories),
    )
    app.state.hearth.clock = house[1].clock
    return TestClient(app), app.state.communications


def test_auth_and_validation_never_echo_params_or_construct_client(house):
    def forbidden(*args):
        raise AssertionError("render constructed a transport")

    client, _ = client_of(house, {"discord": forbidden})
    for path in (
        "",
        "/conversations",
        "/conversations/missing",
        "/deliveries",
        "/deliveries/missing",
        "/usage",
        "/forwarding",
    ):
        assert client.get(ROOT + path).status_code == 401
    for path in ("", "/conversations", "/deliveries", "/usage", "/forwarding"):
        result = client.get(ROOT + path, headers=AUTH)
        assert result.status_code == 200, result.text
        assert "synthetic-bot-secret" not in result.text
        assert str(house[8].root) not in result.text
    value = client.post(
        ROOT + "/connections/bot/probe", headers=AUTH, json={"route_id": {"token": "LEAK_ME"}}
    )
    assert value.status_code == 422
    assert "LEAK_ME" not in value.text and "input" not in value.text
    invalid = client.put(
        ROOT + "/configuration/connection/new",
        headers=AUTH,
        json={"expected_revision": 0, "value": {"token": "LEAK_ME"}},
    )
    assert invalid.status_code == 409 and "LEAK_ME" not in invalid.text


def test_pending_configuration_revision_conflict_and_activation_ack(house):
    client, _ = client_of(house)
    value = {
        "transport": "discord",
        "bot_id": "new-account",
        "secret_ref": "missing",
        "state": "active",
        "label": "Pending installation",
    }
    path = ROOT + "/configuration/connection/new"
    assert (
        client.put(path, headers=AUTH, json={"expected_revision": 0, "value": value}).status_code
        == 200
    )
    assert client.put(path, headers=AUTH, json={"expected_revision": 0, "value": value}).json() == {
        "error": "revision_conflict"
    }
    items = client.get(ROOT, headers=AUTH).json()["configuration"]
    assert next(i for i in items if i["id"] == "new")["value"]["state"] == "pending"
    path = ROOT + "/connections/bot/activate"
    assert client.post(
        path, headers=AUTH, json={"expected_revision": 0, "old_consumer_stopped": False}
    ).json() == {"error": "delivery_consumer_stop_required"}
    assert (
        client.post(
            path, headers=AUTH, json={"expected_revision": 0, "old_consumer_stopped": True}
        ).status_code
        == 200
    )
    assert client.post(
        path, headers=AUTH, json={"expected_revision": 0, "old_consumer_stopped": True}
    ).json() == {"error": "revision_conflict"}
    status = client.get(ROOT, headers=AUTH).json()
    assert status["bindings"] == [
        {"connection_id": "bot", "revision": 1, "activated_at": house[2][0]}
    ]


def test_bounded_transcript_redacts_embedded_owner_and_usage_is_independent(house):
    client, worker = client_of(house)
    accepted = house[7].submit(message(house, text="A retained inbound question"))
    run = house[1].admit(accepted["task_id"], reserve=10000)
    with house[1].database.transaction(write=True) as db:
        db.execute(
            "UPDATE chat_turns SET text=? WHERE task_id=?",
            ("prefix" + run.owner_token + "suffix synthetic-bot-secret", run.task_id),
        )
    response = client.get(ROOT + "/conversations?limit=1", headers=AUTH).json()
    assert len(response["items"]) == 1 and response["items"][0]["busy"] == 1
    assert "text" not in json.dumps(response)
    identity = response["items"][0]["id"]
    detail = client.get(ROOT + f"/conversations/{identity}", headers=AUTH)
    assert run.owner_token not in detail.text and "synthetic-bot-secret" not in detail.text
    turn = detail.json()["turns"][0]
    assert turn["text"] == "prefix[redacted]suffix [redacted]"
    assert turn["run_id"] == run.id and turn["actual_cost"] is None and not turn["usage_known"]
    usage = client.get(ROOT + "/usage", headers=AUTH).json()["origins"][0]
    assert usage["origin"] == "conversation" and usage["active_runs"] == 1
    assert usage["known_cost"] == 0 and "instruction" not in usage
    assert client.get(ROOT + "/conversations?limit=101", headers=AUTH).status_code == 409
    assert (
        client.get(ROOT + f"/conversations/{identity}?before=missing", headers=AUTH).status_code
        == 409
    )
    assert worker.health()["communications"] == "stopped"


def test_transcript_entire_envelope_byte_bound_and_cursor_progress(house):
    client, _ = client_of(house)
    for index in range(8):
        accepted = house[7].submit(message(house, str(index), text="a" * 5900))
        run = house[1].admit(accepted["task_id"], reserve=10000)
        bridge_of(house[0], run, str(index))
        settle(house[0], run, text="HEARTH_QUIET")
        from hearth.channels.chat.reply import Replies

        Replies(house[1], house[8]).prepare(accepted["turn_id"])
    identity = client.get(ROOT + "/conversations", headers=AUTH).json()["items"][0]["id"]
    seen = []
    before = ""
    while True:
        result = client.get(
            ROOT + f"/conversations/{identity}" + (f"?before={before}" if before else ""),
            headers=AUTH,
        ).json()
        assert len(json.dumps(result, ensure_ascii=True).encode()) <= 32768
        seen.extend(t["id"] for t in result["turns"])
        before = result["next_before"]
        if before is None:
            break
    assert len(seen) == len(set(seen)) == 8


def test_unknown_delivery_has_exact_evidence_and_requires_explicit_resolution(house):
    delivery, forwarding, _ = setup(house)
    identity = notice(house)
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
    client, _ = client_of(house)
    listing = client.get(ROOT + "/deliveries?kind=notification", headers=AUTH).json()["items"]
    assert listing[0]["state"] == "unknown" and listing[0]["source_id"] == identity
    path = ROOT + "/deliveries/" + permit.operation_id
    detail = client.get(path, headers=AUTH).json()
    assert detail["actions"] == ["sent", "not_sent", "abandon", "reissue"]
    assert "owner" not in json.dumps(detail) and "store_path" not in json.dumps(detail)
    assert detail["attempts"][0]["id"] == permit.attempt_id
    for action in ("sent", "not_sent", "reissue", "cancel"):
        result = client.post(
            path + "/resolve",
            headers=AUTH,
            json={"expected_revision": detail["revision"], "action": action, "reason": "observed"},
        )
        assert result.status_code == 409
    body = {
        "expected_revision": detail["revision"],
        "action": "sent",
        "reason": "Matched external receipt",
        "evidence": receipt(permit).model_dump(),
    }
    assert client.post(path + "/resolve", headers=AUTH, json=body).status_code == 200
    resolved = client.get(path, headers=AUTH).json()
    assert resolved["state"] == "confirmed" and resolved["resolutions"][0]["action"] == "sent"
    assert client.post(path + "/resolve", headers=AUTH, json=body).json() == {
        "error": "revision_conflict"
    }
    with house[1].database.transaction() as db:
        assert (
            db.execute("SELECT read_at FROM notifications WHERE id=?", (identity,)).fetchone()[0]
            is None
        )


def test_config_revocation_immediately_refuses_queued_preserves_unknown(house):
    delivery, forwarding, _ = setup(house)
    notice(house, identity="one")
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
    notice(house, identity="two")
    forwarding.enqueue("forward")
    client, _ = client_of(house)
    value = Connection(
        transport="discord", bot_id="bot-account", secret_ref="bot", state="disabled"
    ).model_dump()
    assert (
        client.put(
            ROOT + "/configuration/connection/bot",
            headers=AUTH,
            json={"expected_revision": 1, "value": value},
        ).status_code
        == 200
    )
    states = {r["id"]: r["state"] for r in delivery.inspect()}
    assert states[permit.operation_id] == "unknown" and list(states.values()).count("refused") == 1


def test_forwarding_filter_and_url_revisions(house):
    client, _ = client_of(house)
    body = {
        "expected_revision": 0,
        "destination": {"connection_id": "bot", "guild_id": "guild", "channel_id": "channel"},
        "kinds": ["run.failed"],
        "enabled": True,
        "operator_url": "https://hearth.example",
    }
    path = ROOT + "/forwarding/notices"
    assert client.put(path, headers=AUTH, json=body).status_code == 200
    body.update(expected_revision=1, operator_url="https://new.example")
    assert client.put(path, headers=AUTH, json=body).status_code == 200
    assert (
        client.get(ROOT + "/forwarding", headers=AUTH).json()["items"][0]["operator_url"]
        == "https://new.example"
    )
    body.update(expected_revision=2, operator_url="https://user:secret@example.test")
    refused = client.put(path, headers=AUTH, json=body)
    assert refused.status_code == 409 and "secret" not in refused.text


def test_held_copy_all_communications_mutations_and_probe_refused(house, tmp_path):
    setup(house)
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    capture(house[1].database.path.parent, backup)
    restore(backup, restored)
    client = TestClient(
        create_app(restored, "synthetic-operator-token", supervise=False, runtime=fake_runtime())
    )
    assert client.get(ROOT, headers=AUTH).json()["read_only"] is True
    actions = [
        ("post", "/connections/bot/probe", {"route_id": "route"}),
        (
            "post",
            "/connections/bot/activate",
            {"expected_revision": 1, "old_consumer_stopped": True},
        ),
        (
            "put",
            "/configuration/grant/herald",
            {"expected_revision": 1, "value": Grant().model_dump()},
        ),
        (
            "post",
            "/deliveries/missing/resolve",
            {"expected_revision": 1, "action": "abandon", "reason": "Stop local work"},
        ),
    ]
    locks_before = sorted(restored.glob("*.lock"))
    for method, path, body in actions:
        result = getattr(client, method)(ROOT + path, headers=AUTH, json=body)
        assert result.status_code == 409 and result.json() == {"error": "restored_copy_read_only"}
    assert sorted(restored.glob("*.lock")) == locks_before


def test_explicit_probe_uses_owned_current_scoped_worker_without_other_io(house):
    calls = []

    class Adapter:
        def probe(self, guild, channel, **kwargs):
            with house[1].database.transaction(write=True):
                pass
            calls.append((guild, channel, kwargs))

        def health(self):
            return {"state": "ready", "content_access": "available"}

        def close(self):
            pass

    client, worker = client_of(house, {"discord": lambda *_: Adapter()})
    path = ROOT + "/connections/bot/probe"
    assert client.post(path, headers=AUTH, json={"route_id": "route"}).json() == {
        "error": "communications_worker_not_owned"
    }
    with worker:
        assert client.post(path, headers=AUTH, json={"route_id": "route"}).status_code == 409
        assert calls == []
        worker.delivery.activate(
            "bot", expected_revision=0, operator_id="operator", old_consumer_stopped=True
        )
        assert client.post(path, headers=AUTH, json={"route_id": "route"}).status_code == 200
        assert len(calls) == 1 and calls[0][2]["read"] and calls[0][2]["send"]
        with house[1].database.transaction() as db:
            for table in ("communications_cursors", "chat_turns", "delivery_operations"):
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        house[3].save("grant", "herald", Grant(), expected_revision=1)
        assert client.post(path, headers=AUTH, json={"route_id": "route"}).json() == {
            "error": "communications_scope_denied"
        }
        assert len(calls) == 1


def test_probe_does_not_report_success_after_concurrent_revocation(house):
    class Adapter:
        def probe(self, *args, **kwargs):
            house[3].save("grant", "herald", Grant(), expected_revision=1)

        def health(self):
            return {"state": "ready"}

        def close(self):
            pass

    _, worker = client_of(house, {"discord": lambda *_: Adapter()})
    worker.delivery.activate(
        "bot", expected_revision=0, operator_id="operator", old_consumer_stopped=True
    )
    with worker, pytest.raises(Refused, match="communications_scope_denied"):
        worker.probe("bot", "route")


def test_legacy_oversized_config_is_omitted_without_clipping_editable_value(house):
    route = house[4].model_copy(update={"operator_ids": ["x" * 100000]})
    house[3].save("route", "route", route, expected_revision=1)
    client, _ = client_of(house)
    status = client.get(ROOT, headers=AUTH).json()
    row = next(r for r in status["configuration"] if r["kind"] == "route")
    assert row == {
        "kind": "route",
        "id": "route",
        "value": None,
        "revision": 2,
        "omitted": "configuration_too_large",
    }
    assert len(json.dumps(status, ensure_ascii=True).encode()) < 524288
    value = house[4].model_dump() | {"operator_ids": ["x" * 129]}
    result = client.put(
        ROOT + "/configuration/route/route",
        headers=AUTH,
        json={"expected_revision": 2, "value": value},
    )
    assert result.json() == {"error": "communications_sender_id_invalid"}


def test_unknown_usage_and_notification_source_navigation(tmp_path):
    app = create_app(
        tmp_path, "synthetic-operator-token", supervise=False, runtime=fake_runtime("unknown_usage")
    )
    from tests.api.test_api import seed_reader_via

    client = TestClient(app, headers=AUTH)
    seed_reader_via(client)
    import time

    task = client.post(
        "/api/tasks",
        headers={**AUTH, "Idempotency-Key": "usage"},
        json={
            "resident_id": "reader",
            "instruction": "Synthetic task",
            "expires_at": int(time.time()) + 3600,
        },
    ).json()
    client.post("/api/tasks/" + task["task_id"] + "/start", headers=AUTH)
    run = app.state.executor.step()[0]
    usage = client.get(ROOT + "/usage", headers=AUTH).json()["origins"][0]
    assert usage["origin"] == "operator" and usage["unknown_runs"] == 1 and usage["known_cost"] == 0
    client.post(
        "/api/runs/" + run.id + "/usage",
        headers={**AUTH, "Idempotency-Key": "report"},
        json={"amount": 2000, "evidence": "Synthetic meter"},
    )
    usage = client.get(ROOT + "/usage", headers=AUTH).json()["origins"][0]
    assert usage["unknown_runs"] == 0 and usage["known_cost"] == 2000
    assert (
        client.put(
            ROOT + "/configuration/connection/notices",
            headers=AUTH,
            json={"expected_revision": 0, "value": {"transport": "ntfy", "secret_ref": "notices"}},
        ).status_code
        == 200
    )
    assert (
        client.put(
            ROOT + "/forwarding/notices",
            headers=AUTH,
            json={
                "expected_revision": 0,
                "destination": {"connection_id": "notices", "target_id": "operator"},
                "kinds": ["run.succeeded", "run.failed"],
                "enabled": True,
            },
        ).status_code
        == 200
    )
    with app.state.hearth.database.transaction() as db:
        through = db.execute("SELECT MAX(sequence) FROM audit").fetchone()[0]
    assert (
        app.state.communications.forwarding.enqueue(
            "notices", backfill_after=0, through_cursor=through
        )
        == 1
    )
    listing = client.get(
        ROOT + "/deliveries?kind=notification&resident_id=reader", headers=AUTH
    ).json()["items"]
    assert listing[0]["run_id"] == run.id
    detail = client.get(ROOT + "/deliveries/" + listing[0]["id"], headers=AUTH).json()
    assert detail["run_id"] == run.id and detail["task_id"] == run.task_id
    assert detail["run"]["actual_cost"] == 2000
    assert detail["notification"]["resource_id"] == run.id


def test_old_late_conflict_remains_reconcilable_after_history_page(house):
    delivery, forwarding, _ = setup(house)
    notice(house)
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        delivery.complete(permit, receipt(permit))
        delivery.complete(permit, receipt(permit, "safe_failure"))
    for _ in range(101):
        delivery.resolve(
            permit.operation_id,
            expected_revision=delivery.detail(permit.operation_id)["revision"],
            operator_id="operator",
            action="reissue",
            reason="Explicit synthetic duplicate risk",
            duplicate_risk_acknowledged=True,
        )
    client, _ = client_of(house)
    path = ROOT + "/deliveries/" + permit.operation_id
    detail = client.get(path, headers=AUTH).json()
    assert detail["uncertain_attempt_ids"] == [permit.attempt_id]
    assert len(detail["resolutions"]) == 100 and detail["resolution_count"] == 102
    assert all(r["action"] != "late_receipt" for r in detail["resolutions"])
    answer = client.post(
        path + "/resolve",
        headers=AUTH,
        json={
            "expected_revision": detail["revision"],
            "action": "not_sent",
            "reason": "Affirmative rejection",
            "evidence": receipt(permit, "safe_failure").model_dump(),
        },
    )
    assert answer.status_code == 200


def test_late_unknown_reply_marks_closed_conversation_busy(house):
    delivery, _, _ = setup(house)
    accepted = house[7].submit(message(house))
    run = house[1].admit(accepted["task_id"], reserve=10000)
    bridge_of(house[0], run, "late-reply")
    settle(house[0], run, text="Synthetic reply")
    delivery.replies.prepare(accepted["turn_id"])
    with house[1].database.transaction(write=True) as db:
        delivery.replies.handoff_in_transaction(
            db, accepted["turn_id"], delivery.enqueue_in_transaction
        )
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        delivery.complete(permit, receipt(permit))
        delivery.complete(permit, receipt(permit, "safe_failure"))
    client, _ = client_of(house)
    conversation = client.get(ROOT + "/conversations", headers=AUTH).json()["items"][0]
    assert conversation["busy"] == 1
    detail = client.get(ROOT + "/conversations/" + conversation["id"], headers=AUTH).json()
    assert detail["turns"][0]["run_status"] == "succeeded"
    assert detail["turns"][0]["state"] == "closed"
    assert detail["turns"][0]["delivery_state"] == "unknown"
    assert house[7].submit(message(house, "later"))["reason"] == "communications_busy"


@pytest.mark.parametrize("transport", ["discord", "telegram", "ntfy"])
def test_each_advertised_transport_configuration_create_update_reload(house, transport):
    client, _ = client_of(house, {})
    connection_id = "matrix-" + transport
    value = {
        "transport": transport,
        "bot_id": None if transport == "ntfy" else "matrix-account",
        "secret_ref": "missing",
        "state": "pending",
        "label": "Initial connection",
    }
    path = ROOT + "/configuration/connection/" + connection_id
    assert (
        client.put(path, headers=AUTH, json={"expected_revision": 0, "value": value}).status_code
        == 200
    )

    def configured(kind, identity):
        return next(
            item["value"]
            for item in client.get(ROOT, headers=AUTH).json()["configuration"]
            if item["kind"] == kind and item["id"] == identity
        )

    assert configured("connection", connection_id) == value | {"revision": 1}
    value.update(label="Renamed connection", secret_ref="bot", state="active")
    assert (
        client.put(path, headers=AUTH, json={"expected_revision": 1, "value": value}).status_code
        == 200
    )
    assert configured("connection", connection_id) == value | {"revision": 2}
    assert (
        client.post(
            ROOT + f"/connections/{connection_id}/activate",
            headers=AUTH,
            json={"expected_revision": 0, "old_consumer_stopped": True},
        ).status_code
        == 200
    )
    if transport == "ntfy":
        destination = {"connection_id": connection_id, "target_id": "operator"}
    else:
        destination = {"connection_id": connection_id, "guild_id": "guild", "channel_id": "channel"}
        route = {
            "connection_id": connection_id,
            "resident_id": "herald",
            "address": {"guild_id": "guild", "channel_id": "channel"},
            "state": "pending",
            "mode": "dedicated",
            "sender_policy": "operators_only",
            "operator_ids": ["sender"],
            "label": "Initial channel",
        }
        route_path = ROOT + "/configuration/route/matrix-route"
        assert (
            client.put(
                route_path, headers=AUTH, json={"expected_revision": 0, "value": route}
            ).status_code
            == 200
        )
        assert configured("route", "matrix-route") == route | {"revision": 1}
        route.update(state="active", label="Renamed channel", operator_ids=["second-sender"])
        assert (
            client.put(
                route_path, headers=AUTH, json={"expected_revision": 1, "value": route}
            ).status_code
            == 200
        )
        assert configured("route", "matrix-route") == route | {"revision": 2}
        grant = {
            "read": [destination],
            "listen": [destination],
            "reply": [destination],
            "post": [destination],
        }
        grant_path = ROOT + "/configuration/grant/herald"
        assert (
            client.put(
                grant_path, headers=AUTH, json={"expected_revision": 1, "value": grant}
            ).status_code
            == 200
        )
        assert configured("grant", "herald") == grant | {"revision": 2}
        grant.update(post=[])
        assert (
            client.put(
                grant_path, headers=AUTH, json={"expected_revision": 2, "value": grant}
            ).status_code
            == 200
        )
        assert configured("grant", "herald") == grant | {"revision": 3}
    forward_path = ROOT + "/forwarding/matrix-forward"
    forward = {
        "expected_revision": 0,
        "destination": destination,
        "kinds": [],
        "enabled": False,
        "operator_url": None,
    }
    assert client.put(forward_path, headers=AUTH, json=forward).status_code == 200
    forward.update(
        expected_revision=1,
        kinds=["run.failed"],
        enabled=True,
        operator_url="https://hearth.example",
    )
    assert client.put(forward_path, headers=AUTH, json=forward).status_code == 200
    found = client.get(ROOT + "/forwarding", headers=AUTH).json()["items"][0]
    assert found["destination"] == destination and found["revision"] == 2 and found["enabled"] == 1
    assert found["kinds"] == ["run.failed"] and found["operator_url"] == "https://hearth.example"
    value.update(state="disabled")
    assert (
        client.put(path, headers=AUTH, json={"expected_revision": 2, "value": value}).status_code
        == 200
    )
    assert configured("connection", connection_id)["state"] == "disabled"
