"""Real SQLite and credential-free loopback exercise the communications lifetime."""

import json
import os
import sqlite3
import threading
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

import pytest
from hearth.channels.chat.model import Address, Destination, Grant
from hearth.channels.delivery.model import Receipt
from hearth.channels.interface import Message
from hearth.channels.polling import Page, RetryLater
from hearth.channels.worker import Worker
from hearth.residents.models import Refused
from hearth.storage.backup import capture, restore
from hearth.storage.database import SCHEMA_VERSION, Database
from hearth.work.service import Hearth

from tests.channels.test_conversations import house as house_fixture
from tests.channels.test_conversations import message
from tests.channels.test_delivery import setup
from tests.work.test_letter_replies import bridge_of, settle

house = house_fixture


@pytest.fixture
def loopback(house):
    state = {"messages": [], "calls": [], "sent": [], "error": None, "closed": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            state["calls"].append((parsed.path, query))
            messages = [m for m in state["messages"] if m.channel_id == query["channel"][0]]
            if parsed.path == "/latest":
                value = str(max([int(m.message_id) for m in messages], default=0))
            else:
                after, through = int(query["after"][0]), int(query["through"][0])
                available = sorted(
                    [m for m in messages if after < int(m.message_id) <= through],
                    key=lambda m: int(m.message_id),
                )
                limit = int(query["limit"][0])
                value = {
                    "messages": [asdict(m) for m in available[:limit]],
                    "complete": len(available) <= limit,
                }
            encoded = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    class Adapter:
        def get(self, path, channel, *, max_bytes, timeout, **query):
            # Prove no worker writer is held during transport calls.
            with house[1].database.transaction(write=True):
                pass
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/{path}?"
                + urlencode({"channel": channel, **query}),
                timeout=timeout,
            ) as response:
                data = response.read(max_bytes + 1)
                assert len(data) <= max_bytes
                return json.loads(data)

        def latest(self, guild, channel, **limits):
            return self.get("latest", channel, **limits)

        def poll(self, guild, channel, **query):
            if state["error"]:
                state["error"](channel)
            value = self.get("poll", channel, **query)
            return Page(tuple(Message(**m) for m in value["messages"]), value["complete"])

        def send(self, permit, **limits):
            with house[1].database.transaction(write=True):
                pass
            state["sent"].append(permit)
            if state.get("send_error"):
                raise OSError("synthetic-bot-secret must never enter health")
            return Receipt(
                attempt_id=permit.attempt_id,
                intent_sha256=permit.intent_sha256,
                outcome="confirmed",
                external_id="sent",
                evidence="loopback_verified",
            )

        def close(self):
            state["closed"] += 1

    def factory(*_):
        return Adapter()

    state["factory"] = factory
    yield state
    server.shutdown()
    thread.join()
    server.server_close()


def worker(house, loopback, *, activate=True):
    if activate:
        setup(house)
    return Worker(house[1], house[8], {"discord": loopback["factory"]})


def add(house, loopback, identity, **changes):
    turn = message(house, str(identity))
    turn = replace(turn, message=replace(turn.message, **changes))
    loopback["messages"].append(turn.message)
    return turn


def tick(house, worker):
    house[2][0] += 10
    worker.step()


def rows(house, table):
    with house[1].database.transaction() as db:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]


def test_chronological_multiple_pages_restart_and_baseline_once(house, loopback):
    add(house, loopback, 10)
    with worker(house, loopback) as running:
        running.step()
        assert not rows(house, "chat_inbound")
        for identity in range(11, 122):
            add(house, loopback, identity)
        tick(house, running)
        first = rows(house, "communications_cursors")[0]
        assert (first["baseline"], first["cursor"], first["through_id"]) == ("10", "60", "121")
        assert len(rows(house, "chat_inbound")) == 50
    with worker(house, loopback, activate=False) as running:
        tick(house, running)
        assert rows(house, "communications_cursors")[0]["cursor"] == "110"
        tick(house, running)
        assert rows(house, "communications_cursors")[0]["through_id"] is None
        tick(house, running)
    inbound = rows(house, "chat_inbound")
    assert len(inbound) == 111
    accepted = [r for r in inbound if json.loads(r["receipt"])["decision"] == "accepted"]
    assert [r["message_id"] for r in accepted] == ["11"]
    assert len(rows(house, "tasks")) == 1
    assert house[7].submit(message(house, "10"))["reason"] == "communications_processed_interval"
    assert len([r for r in rows(house, "audit") if r["kind"] == "communications.baseline"]) == 1


def test_reply_handoff_one_operation_unknown_never_resent(house, loopback):
    with worker(house, loopback) as running:
        running.step()
        add(house, loopback, 1)
        tick(house, running)
        run = house[1].run(rows(house, "chat_turns")[0]["run_id"])
        bridge_of(house[0], run, "worker-reply")
        settle(house[0], run, text="A bounded reply synthetic-bot-secret")
        loopback["send_error"] = True
        tick(house, running)
        assert running.delivery.inspect()[0]["state"] == "unknown"
        assert "synthetic-bot-secret" not in loopback["sent"][0].intent.text
        tick(house, running)
    with worker(house, loopback, activate=False) as running:
        tick(house, running)
        assert running.delivery.inspect()[0]["state"] == "unknown"
    assert len(loopback["sent"]) == 1
    assert len(rows(house, "delivery_operations")) == 1


def test_two_workers_and_delivery_owner_cannot_compete(house, loopback):
    with worker(house, loopback) as first:
        first.step()
        with pytest.raises(Refused, match="communications_worker_owned"):
            with worker(house, loopback, activate=False):
                pass
        with pytest.raises(Refused, match="delivery_worker_owned"):
            with first.delivery.worker("bot"):
                pass


def test_partial_failure_route_removal_and_full_rate_delay(house, loopback):
    route, grant = house[4:6]
    address = Address(guild_id="guild", channel_id="other")
    dest = Destination(connection_id="bot", **address.model_dump())
    house[3].save(
        "route", "route2", route.model_copy(update={"address": address}), expected_revision=0
    )
    house[3].save(
        "grant",
        "herald",
        Grant(read=grant.read + [dest], listen=grant.listen + [dest], reply=grant.reply + [dest]),
        expected_revision=1,
    )
    with worker(house, loopback) as running:
        running.step()
        add(house, loopback, 1)
        add(house, loopback, 2, channel_id="other")

        def failure(channel):
            if channel == "channel":
                raise RetryLater(1000)

        loopback["error"] = failure
        tick(house, running)
        assert len(rows(house, "chat_inbound")) == 1
        schedule = {r["id"]: r for r in rows(house, "communications_schedule")}
        assert schedule["route"]["eligible_at"] == house[2][0] + 1000
        assert rows(house, "communications_cursors")[0]["cursor"] == "0"
        house[3].save(
            "route", "route", route.model_copy(update={"state": "disabled"}), expected_revision=1
        )
        calls = len(loopback["calls"])
        tick(house, running)
        assert all(q["channel"] == ["other"] for _, q in loopback["calls"][calls:])
        assert loopback["closed"] >= 1


def test_held_restore_no_secret_factory_probe_or_io(house, loopback, tmp_path):
    with worker(house, loopback) as running:
        running.step()
    capture(house[1].database.path.parent, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    held = Hearth(Database(tmp_path / "held" / "hearth.db"))
    calls = len(loopback["calls"])
    with pytest.raises(Refused, match="restored_copy_read_only"):
        with Worker(held, house[8], {"discord": lambda *_: pytest.fail("factory on held copy")}):
            pass
    assert len(loopback["calls"]) == calls


@pytest.mark.parametrize("committed", [False, True])
def test_real_process_death_on_both_sides_of_cursor_commit(house, loopback, committed):
    with worker(house, loopback) as running:
        running.step()
    add(house, loopback, 1)
    pid = os.fork()
    if pid == 0:
        running = worker(house, loopback, activate=False)
        running.__enter__()
        submit = running.conversations.submit_turn_in_transaction

        def die(db, verified):
            result = submit(db, verified)
            os._exit(31)
            return result

        if not committed:
            running.conversations.submit_turn_in_transaction = die
        tick(house, running)
        os._exit(32)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == (32 if committed else 31)
    assert len(rows(house, "chat_inbound")) == int(committed)
    with worker(house, loopback, activate=False) as running:
        tick(house, running)
    assert len(rows(house, "chat_inbound")) == len(rows(house, "tasks")) == 1


def test_populated_v15_upgrade_and_cursor_backup(house, loopback, tmp_path):
    setup(house)
    house[7].submit(message(house))
    path = house[1].database.path
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE communications_cursors")
        db.execute("DROP TABLE communications_schedule")
        db.execute("PRAGMA user_version=15")
    house[1].database.initialize()
    assert path.with_name("hearth.db.before-v15").exists()
    assert len(rows(house, "tasks")) == 1
    with house[1].database.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    house[2][0] += 301
    with worker(house, loopback, activate=False) as running:
        running.step()
    capture(path.parent, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "held")
    with Database(tmp_path / "held" / "hearth.db").transaction() as db:
        assert db.execute("SELECT baseline FROM communications_cursors").fetchone()[0] == "0"


def test_replay_after_pruning_and_changed_payload_decides_whole_page(house, loopback):
    from hearth.channels.chat.transcripts import prune

    with worker(house, loopback) as running:
        running.step()
        original = message(house, "1")
        accepted = house[7].submit(original)
        # An already closed transcript may be pruned before the poll worker sees replay.
        run = house[1].admit(accepted["task_id"], reserve=10_000)
        bridge_of(house[0], run, "pruned")
        settle(house[0], run, text="HEARTH_QUIET")
        running.replies.prepare(accepted["turn_id"])
        house[2][0] += 31 * 86400
        with house[1].database.transaction(write=True) as db:
            prune(db, "route", house[2][0])
        add(house, loopback, 1, text="Edited message must not become another task")
        add(house, loopback, 2, mentions_bot=False)
        tick(house, running)
        assert rows(house, "communications_cursors")[0]["cursor"] == "2"
        assert len(rows(house, "tasks")) == 1
        assert house[7].submit(original) == accepted
        assert [r for r in rows(house, "audit") if r["kind"] == "communications.inbound_conflict"]


def test_secret_reload_preserves_server_delay_and_revocation_blocks_admission(house, loopback):
    with worker(house, loopback) as running:
        running.step()
        add(house, loopback, 1)

        def rate_limit(_):
            raise RetryLater(1000, connection_wide=True)

        loopback["error"] = rate_limit
        tick(house, running)
        deadline = [r for r in rows(house, "communications_schedule") if r["kind"] == "connection"][
            0
        ]["eligible_at"]
        path = house[8].root / "bot"
        path.unlink()
        tick(house, running)
        assert [r for r in rows(house, "communications_schedule") if r["kind"] == "connection"][0][
            "eligible_at"
        ] == deadline
        path.write_text("rotated-synthetic-secret")
        path.chmod(0o600)
        loopback["error"] = None
        calls = len(loopback["calls"])
        tick(house, running)
        assert len(loopback["calls"]) == calls
        assert not rows(house, "tasks")


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"created_at": 1}, "communications_stale"),
        ({"human": False}, "communications_nonhuman"),
        ({"mentions_bot": False}, "communications_unmentioned"),
    ],
)
def test_rejected_messages_commit_progress_without_bodies(house, loopback, changes, reason):
    with worker(house, loopback) as running:
        running.step()
        add(house, loopback, 1, **changes)
        tick(house, running)
    assert rows(house, "communications_cursors")[0]["cursor"] == "1"
    assert json.loads(rows(house, "chat_inbound")[0]["receipt"])["reason"] == reason
    assert not rows(house, "tasks")


def test_revocation_during_page_refuses_progress_and_tasks(house, loopback):
    with worker(house, loopback) as running:
        running.step()
        add(house, loopback, 1)

        def revoke(_):
            house[3].save("grant", "herald", Grant(), expected_revision=1)

        loopback["error"] = revoke
        tick(house, running)
        assert rows(house, "communications_cursors")[0]["cursor"] == "0"
        assert not rows(house, "tasks")


def test_shutdown_releases_every_owner_when_client_close_raises(house, loopback):
    from hearth.channels.chat.model import Connection

    running = worker(house, loopback)
    house[3].save(
        "connection",
        "bot2",
        Connection(transport="discord", bot_id="second", secret_ref="bot", state="active"),
        expected_revision=0,
    )
    running.delivery.activate(
        "bot2", expected_revision=0, operator_id="operator", old_consumer_stopped=True
    )
    factory = loopback["factory"]

    def create(connection, secret):
        adapter = factory(connection, secret)
        if connection["bot_id"] == "bot-account":

            def broken_close():
                raise OSError("synthetic close failure")

            adapter.close = broken_close
        return adapter

    running.factories["discord"] = create
    with running:
        running.step()
    with running.delivery.worker("bot2"):
        pass
    with running.delivery.worker("bot"):
        pass


@pytest.mark.parametrize("connection_wide", [False, True])
def test_send_rate_scope_defers_before_permit_and_survives_restart(
    house, loopback, connection_wide
):
    from hearth.channels.delivery.notifications import Forwarding
    from hearth.channels.polling import SendResult

    from tests.channels.test_delivery import notice

    delivery, forwarding, _ = setup(house)
    other = Destination(connection_id="bot", guild_id="guild", channel_id="other")
    Forwarding(delivery).configure(
        "forward-other", other, kinds=["run.succeeded"], enabled=True, expected_revision=0
    )
    notice(house, identity="first")
    notice(house, identity="second")
    forwarding.enqueue("forward")
    factory = loopback["factory"]

    def create(*args):
        adapter = factory(*args)

        def send(permit, **limits):
            loopback["sent"].append(permit)
            return SendResult(
                Receipt(
                    attempt_id=permit.attempt_id,
                    intent_sha256=permit.intent_sha256,
                    outcome="safe_failure",
                    evidence="rate_limited",
                    retry_after=1000,
                ),
                connection_wide,
            )

        adapter.send = send
        return adapter

    loopback["factory"] = create
    with worker(house, loopback, activate=False) as running:
        running.step()
        tick(house, running)
        assert len(loopback["sent"]) == 1
    Forwarding(delivery).enqueue("forward-other")
    with worker(house, loopback, activate=False) as running:
        tick(house, running)
    assert len(loopback["sent"]) == (1 if connection_wide else 2)
    assert len(rows(house, "delivery_attempts")) == len(loopback["sent"])
    if not connection_wide:
        assert loopback["sent"][-1].intent.destination.channel_id == "other"


def test_secret_revocation_during_page_prevents_admission_and_send(house, loopback):
    from tests.channels.test_delivery import notice

    delivery, forwarding, _ = setup(house)
    with worker(house, loopback, activate=False) as running:
        running.step()
        notice(house)
        forwarding.enqueue("forward")
        add(house, loopback, 1)

        def revoke(_):
            (house[8].root / "bot").unlink()

        loopback["error"] = revoke
        tick(house, running)
        assert not rows(house, "tasks")
        assert not rows(house, "delivery_attempts")
        assert not loopback["sent"]
        assert delivery.inspect()[0]["state"] == "queued"


@pytest.mark.parametrize("connection_wide", [False, True])
@pytest.mark.parametrize("raised", [False, True])
def test_process_death_preserves_rate_delay_before_receipt(
    house, loopback, connection_wide, raised
):
    from hearth.channels.polling import SendResult

    from tests.channels.test_delivery import notice

    _, forwarding, _ = setup(house)
    notice(house, identity="first")
    notice(house, identity="second")
    assert forwarding.enqueue("forward") == 2
    factory = loopback["factory"]

    def create(*args):
        adapter = factory(*args)

        def send(permit, **limits):
            if raised:
                raise RetryLater(1000, connection_wide=connection_wide)
            return SendResult(
                Receipt(
                    attempt_id=permit.attempt_id,
                    intent_sha256=permit.intent_sha256,
                    outcome="safe_failure",
                    evidence="rate_limited",
                    retry_after=1000,
                ),
                connection_wide,
            )

        adapter.send = send
        return adapter

    loopback["factory"] = create
    pid = os.fork()
    if pid == 0:
        running = worker(house, loopback, activate=False)
        running.__enter__()
        running.delivery.complete = lambda *_: os._exit(41)
        running.step()
        os._exit(42)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 41
    with worker(house, loopback, activate=False) as running:
        tick(house, running)
        assert sorted(r["state"] for r in rows(house, "delivery_operations")) == [
            "queued",
            "unknown",
        ]
        assert len(rows(house, "delivery_attempts")) == 1


def test_stopping_waits_for_inflight_transport_before_releasing_lifetime(house, loopback):
    entered, release = threading.Event(), threading.Event()
    factory = loopback["factory"]

    def create(*args):
        adapter = factory(*args)
        latest = adapter.latest

        def blocked(*args, **limits):
            entered.set()
            assert release.wait(5)
            return latest(*args, **limits)

        adapter.latest = blocked
        return adapter

    loopback["factory"] = create
    running = worker(house, loopback)
    running.start()
    assert entered.wait(5)
    stopper = threading.Thread(target=running.stop)
    stopper.start()
    try:
        with pytest.raises(Refused, match="communications_worker_owned"):
            with worker(house, loopback, activate=False):
                pass
        assert stopper.is_alive()
    finally:
        release.set()
        stopper.join(5)
    assert not stopper.is_alive()
    with worker(house, loopback, activate=False):
        pass
