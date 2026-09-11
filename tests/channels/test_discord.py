"""Discord's documented newest-first REST pages against real SQLite owners."""

import json
import socket
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from hearth.channels.chat.model import Address, Connection, Destination, Grant, Route
from hearth.channels.discord import Discord
from hearth.channels.discord.client import HISTORY, SEND, USER_AGENT, VIEW, Unavailable
from hearth.channels.worker import Worker
from hearth.storage.backup import capture, restore

from tests.channels.test_conversations import house as house_fixture
from tests.channels.test_worker import rows, tick
from tests.work.test_letter_replies import bridge_of, settle

house = house_fixture


@pytest.fixture
def discord(house):
    config = house[3]
    config.save(
        "connection",
        "bot",
        Connection(transport="discord", bot_id="bot-account", secret_ref="bot", state="disabled"),
        expected_revision=1,
    )
    config.save(
        "connection",
        "discord",
        Connection(transport="discord", bot_id="100", secret_ref="bot", state="active"),
        expected_revision=0,
    )
    dest = Destination(connection_id="discord", guild_id="200", channel_id="300")
    config.save(
        "grant",
        "herald",
        Grant(read=[dest], listen=[dest], reply=[dest], post=[dest]),
        expected_revision=1,
    )
    config.save(
        "route",
        "discord-route",
        Route(
            connection_id="discord",
            resident_id="herald",
            address=Address(guild_id="200", channel_id="300"),
            sender_policy="guild_channel_humans",
            state="active",
        ),
        expected_revision=0,
    )
    state = {
        "messages": [],
        "calls": [],
        "sent": [],
        "overwrites": [],
        "flags": 1 << 19,
        "fault": None,
        "bot": "100",
        "guild": "200",
        "channel_type": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            with house[1].database.transaction(write=True):
                pass
            parsed = urlsplit(self.path)
            path = parsed.path.removeprefix("/api/v10")
            query = parse_qs(parsed.query)
            state["calls"].append((self.command, path, query, dict(self.headers)))
            if state["fault"]:
                fault = state["fault"](self.command, path)
                if fault is not None:
                    status, value, headers = fault
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(
                        value if isinstance(value, bytes) else json.dumps(value).encode()
                    )
                    return
            if path == "/users/@me":
                value = {"id": state["bot"], "bot": True}
            elif path == "/channels/300":
                value = {
                    "id": "300",
                    "guild_id": state["guild"],
                    "type": state["channel_type"],
                    "permission_overwrites": state["overwrites"],
                }
            elif path == "/guilds/200":
                value = {
                    "id": "200",
                    "owner_id": "999",
                    "roles": [{"id": "200", "permissions": str(VIEW | SEND | HISTORY)}],
                }
            elif path == "/guilds/200/members/100":
                value = {"user": {"id": "100"}, "roles": []}
            elif path == "/applications/@me":
                value = {"flags": state["flags"]}
            elif path == "/channels/300/messages" and self.command == "GET":
                available = sorted(state["messages"], key=lambda m: int(m["id"]), reverse=True)
                if "before" in query:
                    available = [m for m in available if int(m["id"]) < int(query["before"][0])]
                # No oldest-N interpretation of 'after' hidden in this fixture.
                assert "after" not in query
                value = available[: int(query["limit"][0])]
            elif path == "/channels/300/messages":
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["sent"].append(value)
                if state.get("lose_send"):
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                value = value | {"id": "900", "channel_id": "300", "author": {"id": "100"}}
            else:
                raise AssertionError(path)
            self.send_response(200)
            if state.get("headers"):
                for k, v in state["headers"](self.command, path).items():
                    self.send_header(k, v)
            if self.command == "POST" and state.get("ack_change"):
                value.update(state["ack_change"])
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}/api/v10"
    state["factory"] = lambda connection, secret: Discord(
        connection, secret, _test_origin=origin, clock=lambda: house[2][0]
    )
    state["client"] = state["factory"]({"bot_id": "100"}, "synthetic-bot-secret")
    yield state
    server.shutdown()
    thread.join()
    server.server_close()


def add(discord, house, identity, **changes):
    value = {
        "id": str(identity),
        "channel_id": "300",
        "author": {"id": "400"},
        "content": "A human request",
        "mentions": [{"id": "100"}],
        "timestamp": datetime.fromtimestamp(house[2][0], UTC).isoformat(),
        "type": 0,
    }
    value.update(changes)
    discord["messages"].append(value)


def worker(house, discord, *, activate=True):
    result = Worker(house[1], house[8], {"discord": discord["factory"]})
    if activate:
        result.delivery.activate(
            "discord", expected_revision=0, operator_id="operator", old_consumer_stopped=True
        )
    return result


def test_reverse_scan_restart_partial_failure_and_chronological_admission(house, discord):
    add(discord, house, 10)
    with worker(house, discord) as running:
        running.step()
        for n in range(11, 132):
            add(discord, house, n)
        tick(house, running)
        assert rows(house, "communications_cursors")[0]["scan_before"] == "83"
        assert rows(house, "communications_cursors")[0]["cursor"] == "10"
        assert not rows(house, "chat_inbound")
    with worker(house, discord, activate=False) as running:
        discord["fault"] = lambda method, path: (503, {}, {}) if path.endswith("messages") else None
        tick(house, running)
        assert rows(house, "communications_cursors")[0]["scan_before"] == "83"
        discord["fault"] = None
        for _ in range(12):
            tick(house, running)
    assert len(rows(house, "chat_inbound")) == 121
    accepted = [
        r for r in rows(house, "chat_inbound") if json.loads(r["receipt"])["decision"] == "accepted"
    ]
    assert [r["message_id"] for r in accepted] == ["11"]
    assert rows(house, "communications_cursors")[0]["cursor"] == "131"
    assert rows(house, "communications_cursors")[0]["scan_before"] is None


@pytest.mark.parametrize("lost", [False, True])
def test_human_reply_and_unknown_response_never_resends(house, discord, lost):
    with worker(house, discord) as running:
        running.step()
        add(discord, house, 1)
        tick(house, running)
        run = house[1].run(rows(house, "chat_turns")[0]["run_id"])
        bridge_of(house[0], run, "discord-reply")
        settle(house[0], run, text="Reply @everyone <@400>")
        discord["lose_send"] = lost
        tick(house, running)
        assert running.delivery.inspect()[0]["state"] == ("unknown" if lost else "confirmed")
    with worker(house, discord, activate=False) as running:
        tick(house, running)
    assert len(discord["sent"]) == 1
    sent = discord["sent"][0]
    assert sent["allowed_mentions"] == {"parse": [], "replied_user": False}
    assert sent["message_reference"] == {
        "type": 0,
        "guild_id": "200",
        "channel_id": "300",
        "message_id": "1",
        "fail_if_not_exists": True,
    }
    assert sent["enforce_nonce"] is True
    assert all(
        c[3]["Authorization"] == "Bot synthetic-bot-secret" and c[3]["User-Agent"] == USER_AGENT
        for c in discord["calls"]
    )


def test_only_structured_human_mentions(house, discord):
    with worker(house, discord) as running:
        running.step()
        for n, changes in enumerate(
            [
                {"mentions": [], "content": "<@100>"},
                {"author": {"id": "400", "bot": True}},
                {"webhook_id": "600"},
                {"type": 7},
                {"author": {"id": "400", "system": True}},
                {"type": 19},
            ],
            1,
        ):
            add(discord, house, n, **changes)
        tick(house, running)
    accepted = [
        r for r in rows(house, "chat_inbound") if json.loads(r["receipt"])["decision"] == "accepted"
    ]
    assert [r["message_id"] for r in accepted] == ["6"]


def test_permission_revocation_identity_and_content_diagnostics(house, discord):
    client = discord["client"]
    discord["flags"] = 0
    add(discord, house, 1)
    history = client.history("200", "300")
    assert history.content_access == "unavailable" and not history.complete
    assert client.latest("200", "300", max_bytes=524288, timeout=10) == "1"
    assert client.health()["content_access"] == "unavailable"
    discord["messages"] = []
    assert client.history("200", "300").permission == "allowed"
    discord["overwrites"] = [{"id": "100", "type": 1, "deny": str(HISTORY), "allow": "0"}]
    with pytest.raises(Unavailable) as error:
        client.history("200", "300")
    assert error.value.code == "permission_denied"
    discord["overwrites"] = []
    discord["guild"] = "201"
    with pytest.raises(Unavailable) as error:
        client.history("200", "300")
    assert error.value.code == "channel_identity_mismatch"


@pytest.mark.parametrize(
    "status,body,headers",
    [
        (302, {}, {"Location": "http://foreign.invalid/"}),
        (200, b"[" * 10000, {}),
        (200, b"x" * 524289, {}),
        (401, {"token": "do-not-persist"}, {}),
    ],
)
def test_bad_response_origin_and_auth(house, discord, status, body, headers):
    discord["fault"] = lambda method, path: (status, body, headers)
    with worker(house, discord) as running:
        running.step()
        count = len(discord["calls"])
        if status == 401:
            tick(house, running)
            assert len(discord["calls"]) == count
            assert rows(house, "communications_schedule")[0]["error"] == "authentication_failed"
    assert len(discord["calls"]) == 1
    assert not rows(house, "communications_cursors")
    with pytest.raises(ValueError):
        Discord({"bot_id": "100"}, "token", _test_origin="https://evil.invalid/api/v10")


@pytest.mark.parametrize("global_limit", [False, True])
def test_full_rate_limit_delay_persisted_before_retry(house, discord, global_limit):
    with worker(house, discord) as running:
        running.step()
        discord["fault"] = lambda method, path: (
            (429, {"retry_after": 90.2, "global": global_limit}, {"X-RateLimit-Bucket": "shared"})
            if path.endswith("messages")
            else None
        )
        tick(house, running)
        schedules = rows(house, "communications_schedule")
        assert any(
            r["eligible_at"] == house[2][0] + 91
            and r["kind"] == ("connection" if global_limit else "destination")
            for r in schedules
        )
        count = len(discord["calls"])
        tick(house, running)
        assert len(discord["calls"]) == count
    with worker(house, discord, activate=False) as running:
        tick(house, running)
        assert len(discord["calls"]) == count


def test_scan_backup_is_body_free_and_held(house, discord, tmp_path):
    with worker(house, discord) as running:
        running.step()
        for n in range(1, 102):
            add(discord, house, n, mentions=[])
        tick(house, running)
    assert not rows(house, "chat_inbound")
    backup = tmp_path / "backup"
    capture(house[1].database.path.parent, backup)
    held = tmp_path / "held"
    restore(backup, held)
    assert rows(house, "communications_cursors")[0]["scan_before"] is not None


@pytest.mark.parametrize("stage", ["GET", "POST"])
@pytest.mark.parametrize("global_limit", [False, True])
def test_send_rate_limit_is_safe_and_durable(house, discord, stage, global_limit):
    with worker(house, discord) as running:
        running.step()
        add(discord, house, 1)
        tick(house, running)
        run = house[1].run(rows(house, "chat_turns")[0]["run_id"])
        bridge_of(house[0], run, "rate-reply")
        settle(house[0], run, text="A reply")
        discord["fault"] = lambda method, path: (
            (429, {"retry_after": 95, "global": global_limit}, {})
            if method == stage and (stage == "POST" or path == "/users/@me")
            else None
        )
        # Prevent the poll preflight from consuming this injected send response.
        running._schedule("route", "discord-route", 200)
        tick(house, running)
        operation = running.delivery.inspect()[0]
        assert operation["state"] == "queued"
        assert rows(house, "delivery_attempts")[0]["state"] == "safe_failure"
        assert not discord["sent"]
        assert any(
            r["eligible_at"] == house[2][0] + 95 for r in rows(house, "communications_schedule")
        )
        count = len(discord["calls"])
        tick(house, running)
        assert len(discord["calls"]) == count
    with worker(house, discord, activate=False) as running:
        tick(house, running)
        assert len(discord["calls"]) == count


def test_bucket_preflight_and_revoke_refuse_without_unknown(house, discord):
    with worker(house, discord) as running:
        running.step()
        add(discord, house, 1)
        tick(house, running)
        run = house[1].run(rows(house, "chat_turns")[0]["run_id"])
        bridge_of(house[0], run, "denied-reply")
        settle(house[0], run, text="A reply")
        discord["overwrites"] = [{"id": "200", "type": 0, "deny": str(SEND), "allow": "0"}]
        tick(house, running)
        assert running.delivery.inspect()[0]["state"] == "refused"
        assert not discord["sent"]
        assert running.health()["connections"]["discord"]["state"] == "permission_denied"


def test_history_bounds_and_malformed_human_flags(house, discord):
    client = discord["client"]
    for n in range(1, 52):
        add(discord, house, n, content="a" * 2000)
    history = client.history("200", "300")
    assert history.truncated and not history.complete
    assert len(history.messages) < 50
    assert history.messages[0].message_id == "51"
    assert history.messages[0].sender_id == "400"
    assert history.messages[0].guild_id == "200"
    discord["messages"][-1]["author"]["bot"] = 0
    with pytest.raises(ValueError):
        client.history("200", "300")


def test_populated_v16_upgrade_preserves_window(house, discord):
    import sqlite3

    from hearth.storage.database import SCHEMA_VERSION, Database

    with worker(house, discord) as running:
        running.step()
        for n in range(1, 52):
            add(discord, house, n)
        tick(house, running)
    with sqlite3.connect(house[1].database.path) as db:
        db.execute("ALTER TABLE communications_cursors DROP COLUMN scan_before")
        db.execute("PRAGMA user_version=16")
    upgraded = Database(house[1].database.path)
    upgraded.initialize()
    with upgraded.transaction() as db:
        row = db.execute("SELECT * FROM communications_cursors").fetchone()
        assert row["cursor"] == "0" and row["through_id"] == "51"
        assert row["scan_before"] is None
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    with worker(house, discord, activate=False) as running:
        for _ in range(5):
            tick(house, running)
    assert len(rows(house, "chat_inbound")) == 51


@pytest.mark.parametrize("headers", [True, False])
def test_absolute_deadline_stops_trickled_response(house, discord, headers):
    import time

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if headers:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                else:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
                for _ in range(30):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.03)
            except OSError:
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    client = Discord(
        {"bot_id": "100"}, "secret", _test_origin=f"http://127.0.0.1:{server.server_port}/api/v10"
    )
    try:
        started = time.monotonic()
        with pytest.raises((OSError, ValueError)):
            client.latest("200", "300", max_bytes=524288, timeout=0.15)
        assert time.monotonic() - started < 0.6
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("send", [False, True])
def test_success_rate_headers_survive_immediate_restart(house, discord, send):
    with worker(house, discord) as running:
        if send:
            running.step()
            add(discord, house, 1)
            tick(house, running)
            run = house[1].run(rows(house, "chat_turns")[0]["run_id"])
            bridge_of(house[0], run, "success-limit")
            settle(house[0], run, text="A reply")
        discord["headers"] = lambda method, path: (
            {
                "X-RateLimit-Bucket": "shared",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": "90.2",
            }
            if path.endswith("messages") and method == ("POST" if send else "GET")
            else {}
        )
        tick(house, running)
        assert any(
            r["eligible_at"] == house[2][0] + 91 for r in rows(house, "communications_schedule")
        )
        if send:
            assert running.delivery.inspect()[0]["state"] == "confirmed"
        calls = len(discord["calls"])
    with worker(house, discord, activate=False) as running:
        tick(house, running)
        assert len(discord["calls"]) == calls


@pytest.mark.parametrize(
    "change",
    [
        {"nonce": None},
        {"nonce": "different"},
        {"message_reference": {"message_id": "999"}},
        {"content": "different"},
    ],
)
def test_unbound_acknowledgement_stays_unknown(house, discord, change):
    with worker(house, discord) as running:
        running.step()
        add(discord, house, 1)
        tick(house, running)
        run = house[1].run(rows(house, "chat_turns")[0]["run_id"])
        bridge_of(house[0], run, "bad-ack")
        settle(house[0], run, text="A reply")
        discord["ack_change"] = change
        tick(house, running)
        assert running.delivery.inspect()[0]["state"] == "unknown"
        tick(house, running)
        assert len(discord["sent"]) == 1


def test_limit_one_does_not_skip_contiguous_backlog(house, discord):
    for n in range(1, 5):
        add(discord, house, n)
    after, scan, seen = "0", None, []
    for _ in range(20):
        page = discord["client"].poll(
            "200",
            "300",
            after=after,
            through="4",
            limit=1,
            max_bytes=524288,
            timeout=10,
            scan_before=scan,
        )
        if page.scan_before:
            scan = page.scan_before
            continue
        seen.extend(m.message_id for m in page.messages)
        after, scan = page.examined_through, None
        if page.complete:
            break
    assert seen == ["1", "2", "3", "4"]


@pytest.mark.parametrize(
    "change", [{"type": False}, {"pinned": 0}, {"tts": None}, {"mention_everyone": "false"}]
)
def test_malformed_message_facts_are_not_humans(house, discord, change):
    add(discord, house, 1, **change)
    with pytest.raises(ValueError):
        discord["client"].history("200", "300")


def test_bucket_keys_respect_guild_and_route_scope(house, discord):
    client = discord["client"]
    discord["fault"] = lambda method, path: (
        200,
        {},
        {
            "X-RateLimit-Bucket": "guild-common",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset-After": "80",
        },
    )
    # Two independently selected guild major IDs never share a local bucket wait.
    for guild in ("200", "201"):
        client._request("GET", f"/guilds/{guild}", **client._limits(524288, 10))
    assert len(discord["calls"]) == 2
    with pytest.raises(Unavailable) as error:
        client._request("GET", "/guilds/200", **client._limits(524288, 10))
    assert error.value.seconds == 80
    assert len(discord["calls"]) == 2


@pytest.mark.parametrize(
    "limited_path,kind", [("/guilds/200", "guild"), ("/channels/300", "channel")]
)
def test_worker_persists_scoped_limits_and_other_guild_progresses(
    house, discord, limited_path, kind
):
    from hearth.channels.chat.config import read
    from hearth.management.authority import digest

    other = Destination(connection_id="discord", guild_id="201", channel_id="301")
    with house[1].database.transaction() as db:
        saved = read(db, "grant", "herald")
        saved.pop("revision")
        grant = Grant.model_validate(saved)
    house[3].save(
        "grant",
        "herald",
        Grant(
            read=[*grant.read, other],
            listen=[*grant.listen, other],
            reply=[*grant.reply, other],
            post=[*grant.post, other],
        ),
        expected_revision=2,
    )
    house[3].save(
        "route",
        "route2",
        Route(
            connection_id="discord",
            resident_id="herald",
            address=Address(guild_id="201", channel_id="301"),
            state="active",
            sender_policy="guild_channel_humans",
        ),
        expected_revision=0,
    )
    extra = {
        "/channels/301": {"id": "301", "guild_id": "201", "type": 0, "permission_overwrites": []},
        "/guilds/201": {
            "id": "201",
            "owner_id": "999",
            "roles": [{"id": "201", "permissions": str(VIEW | SEND | HISTORY)}],
        },
        "/guilds/201/members/100": {"user": {"id": "100"}, "roles": []},
        "/channels/301/messages": [],
    }
    discord["fault"] = lambda method, path: (200, extra[path], {}) if path in extra else None
    discord["headers"] = lambda method, path: (
        {
            "X-RateLimit-Bucket": "scoped",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset-After": "80",
        }
        if path == limited_path
        else {}
    )
    with worker(house, discord) as running:
        running.step()
        assert {r["channel_id"] for r in rows(house, "communications_cursors")} == {"300", "301"}
        schedules = rows(house, "communications_schedule")
        assert any(
            r["kind"] == kind
            and r["id"] == digest(["discord", "200" if kind == "guild" else "300"])
            for r in schedules
        )
        assert not any(r["kind"] == "connection" for r in schedules)
        assert running._destination_waiting("discord", {"guild_id": "200", "channel_id": "300"})
        assert not running._destination_waiting("discord", {"guild_id": "201", "channel_id": "301"})
    discord["calls"].clear()
    with worker(house, discord, activate=False) as running:
        tick(house, running)
    paths = [c[1] for c in discord["calls"]]
    assert "/channels/301/messages" in paths
    assert "/channels/300" not in paths and "/guilds/200" not in paths


def test_slow_dns_deadline_has_no_late_http(house, discord, monkeypatch):
    import time

    entered, release = threading.Event(), threading.Event()
    original = socket.getaddrinfo

    def slow(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    client = discord["client"]
    started = time.monotonic()
    try:
        with pytest.raises(Unavailable) as error:
            client.latest("200", "300", max_bytes=524288, timeout=0.05)
        assert error.value.code == "dns_timeout"
        assert entered.is_set() and time.monotonic() - started < 0.5
        assert not discord["calls"]
        # A second deadline reuses the same DNS-only operation, not another thread.
        pending = client._dns_pending
        with pytest.raises(Unavailable):
            client.latest("200", "300", max_bytes=524288, timeout=0.05)
        assert client._dns_pending is pending
    finally:
        release.set()
    pending.get(timeout=1)
    assert not discord["calls"]


def test_slow_dns_send_keeps_real_delivery_queued(house, discord, monkeypatch):
    release = threading.Event()
    original = socket.getaddrinfo

    def slow(*args, **kwargs):
        release.wait(2)
        return original(*args, **kwargs)

    with worker(house, discord) as running:
        running.step()
        add(discord, house, 1)
        tick(house, running)
        turn = rows(house, "chat_turns")[0]
        run = house[1].run(turn["run_id"])
        bridge_of(house[0], run, "dns-reply")
        settle(house[0], run, text="A reply")
        running.replies.prepare(turn["id"])
        with house[1].database.transaction(write=True) as db:
            running.replies.handoff_in_transaction(
                db, turn["id"], running.delivery.enqueue_in_transaction
            )
        permit = running.delivery.prepare("discord", running._sessions["discord"][1])
        count = len(discord["calls"])
        monkeypatch.setattr(socket, "getaddrinfo", slow)
        client = discord["client"]
        try:
            result = client.send(permit, max_bytes=524288, timeout=0.05)
            assert result.receipt.outcome == "safe_failure"
            assert result.receipt.evidence == "dns_timeout"
            assert result.receipt.retry_after == 5
            running.delivery.complete(permit, result.receipt)
            assert running.delivery.inspect()[0]["state"] == "queued"
            assert rows(house, "delivery_attempts")[0]["state"] == "safe_failure"
            assert len(discord["calls"]) == count and not discord["sent"]
        finally:
            release.set()
        client._dns_pending.get(timeout=1)
        assert len(discord["calls"]) == count
