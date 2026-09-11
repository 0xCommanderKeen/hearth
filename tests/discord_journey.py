"""Synthetic installed-package journey; no pytest or production runtime dependency."""

import json
import socket
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from hearth.app import create_app
from hearth.channels.chat.config import Configuration, Secrets
from hearth.channels.chat.model import Address, Connection, Destination, Grant, Route
from hearth.channels.discord import Discord
from hearth.channels.discord.client import HISTORY, SEND, VIEW
from hearth.channels.inspection import Inspection
from hearth.channels.worker import Worker
from hearth.execution.usage import binding
from hearth.integrations.codex.usage import publish
from hearth.management.bridge import BoundRun, Bridge
from hearth.observation.notifications import Inbox
from hearth.residents.models import Declaration, Refused
from hearth.storage.backup import capture, restore
from hearth.storage.database import Database
from hearth.work.routines import Routines
from hearth.work.service import Hearth

from tests.fake_native_runtime import FakeNativeRuntime, native_terminal
from tests.fake_runtime import BINARY, KIND, FakeRuntime

SECRET = "synthetic-discord-journey-credential"
DEST = dict(connection_id="discord", guild_id="200", channel_id="300")
READ, POST = "hearth_read_channel_history", "hearth_publish_announcement"


class JourneyRuntime(FakeNativeRuntime):
    """Ordinary runtime evidence, explicitly completed after native tool calls."""

    def bridge(self, run_id):
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
        return Bridge(
            self.service, BoundRun(run_id, row["owner_token"], epoch, row["input_digest"])
        )

    def start(self, run_id, instruction):
        FakeRuntime.start(self, run_id, instruction)
        bridge = self.bridge(run_id)
        bridge.bind_thread("journey-thread")
        bridge.bind_turn("journey-thread", "journey-turn")
        with self.database.transaction(write=True) as db:
            db.execute(
                "UPDATE run_management SET catalog_sha256=?,tools_sha256=? WHERE run_id=?",
                ("b" * 64, "c" * 64, run_id),
            )

    def call(self, run_id, call_id, tool, args):
        result = self.bridge(run_id).call(
            dict(
                threadId="journey-thread",
                turnId="journey-turn",
                callId=call_id,
                tool=tool,
                arguments=args,
            )
        )
        return result["success"], json.loads(result["contentItems"][0]["text"])

    def complete(self, run_id, *, known=True):
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            bound = binding(db, row)
            pin = db.execute("SELECT * FROM run_management WHERE run_id=?", (run_id,)).fetchone()
        terminal = native_terminal(pin)
        if not known:
            terminal["events"] = [
                e for e in terminal["events"] if e["method"] != "thread/tokenUsage/updated"
            ]
        publish(
            self.folder(run_id) / "receipt.json",
            dict(
                kind=KIND,
                protocol="management",
                binding=asdict(bound),
                binary=BINARY,
                terminal=terminal,
            ),
        )


@contextmanager
def discord_api(clock):
    state = dict(
        messages=[],
        calls=[],
        sent=[],
        flags=1 << 19,
        permissions=VIEW | SEND | HISTORY,
        rate=False,
        lose=False,
        sequence=10,
        errors=[],
    )

    def add(**changes):
        state["sequence"] += 1
        value = dict(
            id=str(state["sequence"]),
            channel_id="300",
            author={"id": "400", "bot": False},
            content="Synthetic human mention",
            mentions=[{"id": "100"}],
            timestamp=datetime.fromtimestamp(clock[0], UTC).isoformat(),
            type=0,
        )
        value.update(changes)
        state["messages"].append(value)
        return value["id"]

    state["add"] = add

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            try:
                assert self.headers["Authorization"] == "Bot " + SECRET
                with state["database"].transaction(write=True):
                    pass  # Network must not hold the owning SQLite writer.
                parsed = urlsplit(self.path)
                path, query = parsed.path.removeprefix("/api/v10"), parse_qs(parsed.query)
                state["calls"].append((self.command, path, query))
                if state["rate"] and path.endswith("/messages"):
                    state["rate"] = False
                    self.reply(429, {"retry_after": 37, "global": True})
                    return
                if path == "/users/@me":
                    value = {"id": "100", "bot": True}
                elif path in ("/channels/300", "/channels/301"):
                    value = dict(
                        id=path.rsplit("/", 1)[1], guild_id="200", type=0, permission_overwrites=[]
                    )
                elif path == "/guilds/200":
                    value = dict(
                        id="200",
                        owner_id="999",
                        roles=[dict(id="200", permissions=str(state["permissions"]))],
                    )
                elif path == "/guilds/200/members/100":
                    value = dict(user={"id": "100"}, roles=[])
                elif path == "/applications/@me":
                    value = {"flags": state["flags"]}
                elif path.endswith("/messages") and self.command == "GET":
                    assert "after" not in query
                    channel = path.split("/")[2]
                    values = sorted(
                        (m for m in state["messages"] if m["channel_id"] == channel),
                        key=lambda m: int(m["id"]),
                        reverse=True,
                    )
                    value = [
                        m
                        for m in values
                        if int(m["id"]) < int(query.get("before", [str(2**64)])[0])
                    ][: int(query["limit"][0])]
                elif path == "/channels/300/messages":
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    assert body["allowed_mentions"] == {"parse": [], "replied_user": False}
                    assert body["enforce_nonce"] and SECRET not in body["content"]
                    state["sent"].append(body)
                    identity = add(
                        author={"id": "100", "bot": True}, content=body["content"], mentions=[]
                    )
                    if state["lose"]:
                        state["lose"] = False
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    value = body | dict(id=identity, channel_id="300", author={"id": "100"})
                else:
                    raise AssertionError(path)
                self.reply(200, value)
            except Exception as error:
                state["errors"].append(type(error).__name__)
                self.reply(500, {"error": "synthetic fixture failure"})

        def reply(self, status, value):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    state["factory"] = lambda connection, secret: Discord(
        connection,
        secret,
        _test_origin=f"http://127.0.0.1:{server.server_port}/api/v10",
        clock=lambda: clock[0],
    )
    try:
        yield state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def journey(root):
    clock, checks = [1788640000], []
    protected = root / "secrets"
    protected.mkdir(mode=0o700)
    (protected / "bot").write_text(SECRET)
    (protected / "bot").chmod(0o600)
    secrets, data = Secrets(protected), root / "house"
    with discord_api(clock) as api:
        instances = []

        def factory(path):
            runtime = JourneyRuntime(path)
            instances.append(runtime)
            return runtime

        def open_app():
            app = create_app(data, "synthetic-operator-token", supervise=False, runtime=factory)
            app.state.hearth.clock = lambda: clock[0]
            instances[-1].service = app.state.hearth
            api["database"] = app.state.hearth.database
            return app, app.state.hearth, instances[-1]

        app, service, runtime = open_app()
        service.save_resident(
            "herald",
            Declaration("Herald", "Synthetic communications", 10000000),
            expected_revision=0,
        )
        config = Configuration(service, secrets)
        config.save(
            "connection",
            "discord",
            Connection(transport="discord", bot_id="100", secret_ref="bot", state="active"),
            expected_revision=0,
        )
        dest = Destination(**DEST)
        private = Destination(**(DEST | {"channel_id": "301"}))
        config.save(
            "grant",
            "herald",
            Grant(read=[dest, private], listen=[dest, private], reply=[dest, private], post=[dest]),
            expected_revision=0,
        )
        for name, channel, policy in [
            ("public", "300", "guild_channel_humans"),
            ("private", "301", "operators_only"),
        ]:
            config.save(
                "route",
                name,
                Route(
                    connection_id="discord",
                    resident_id="herald",
                    address=Address(guild_id="200", channel_id=channel),
                    sender_policy=policy,
                    operator_ids=["444"],
                    state="active",
                ),
                expected_revision=0,
            )
        worker = Worker(service, secrets, {"discord": api["factory"]})
        worker.delivery.activate(
            "discord", expected_revision=0, operator_id="operator", old_consumer_stopped=True
        )

        def rows(table):
            with service.database.transaction() as db:
                return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]

        def tick(count=1):
            for _ in range(count):
                clock[0] += 10
                worker.step()
                assert not api["errors"], api["errors"]

        def start(task):
            run = service.admit(task, reserve=10000)
            app.state.executor.step()
            return run.id

        def finish(run, known=True):
            runtime.complete(run, known=known)
            app.state.executor.step()
            result = next(r for r in rows("runs") if r["id"] == run)
            assert result["status"] == "succeeded", result
            assert bool(result["usage_known"]) == known
            assert result["actual_cost"] > 0 if known else result["actual_cost"] is None

        baseline = api["add"](mentions=[], content="Selected synthetic history")
        with worker:
            tick()
            operator = start(
                service.submit(
                    "operator", "herald", "Read and announce", expires_at=clock[0] + 600
                ).task_id
            )
            args = dict(operation_id="history", destination=DEST)
            assert runtime.call(operator, "history", READ, args)[0]
            tick(2)
            ok, history = runtime.call(operator, "history", READ, args)
            assert ok and history["messages"][0]["message_id"] == baseline
            calls = len(api["calls"])
            assert not runtime.call(
                operator,
                "outside",
                READ,
                dict(operation_id="outside", destination=DEST | {"channel_id": "999"}),
            )[0]
            assert len(api["calls"]) == calls
            checks.append("selected_history_and_unselected_scope")
            api["flags"] = 0
            args = dict(operation_id="missing", destination=DEST)
            assert runtime.call(operator, "missing", READ, args)[0]
            tick(2)
            assert runtime.call(operator, "missing", READ, args) == (
                False,
                {"error": "communications_content_unavailable"},
            )
            api["flags"] = 1 << 19
            api["permissions"] = 0
            tick()
            assert worker.health()["connections"]["discord"]["state"] == "permission_denied"
            assert len(rows("tasks")) == 1
            api["permissions"] = VIEW | SEND | HISTORY
            clock[0] += 301
            tick()
            checks.append("content_and_permission_refusals")
            ok, intent = runtime.call(
                operator,
                "announce",
                POST,
                dict(
                    operation_id="announce",
                    destination=DEST,
                    text="Synthetic operator announcement",
                ),
            )
            assert ok
            finish(operator)
            tick(2)
            assert worker.delivery.detail(intent["delivery_id"])["state"] == "confirmed"
            checks.append("operator_announcement")
            routines = Routines(service)
            saved = routines.save(
                "daily",
                "herald",
                "Synthetic routine announcement",
                local_time="09:00",
                timezone="UTC",
                enabled=True,
                expected_revision=0,
            )
            clock[0] = saved["next_at"]
            routine = start(routines.tick()[0])
            ok, intent = runtime.call(
                routine,
                "announce",
                POST,
                dict(
                    operation_id="announce", destination=DEST, text="Synthetic routine announcement"
                ),
            )
            assert ok
            tick(2)
            assert worker.delivery.detail(intent["delivery_id"])["state"] == "confirmed"
            ok, revoked = runtime.call(
                routine,
                "revoke",
                POST,
                dict(operation_id="revoke", destination=DEST, text="Must never escape"),
            )
            assert ok
            config.save(
                "grant",
                "herald",
                Grant(read=[dest, private], listen=[dest, private], reply=[dest, private]),
                expected_revision=1,
            )
            assert worker.delivery.detail(revoked["delivery_id"])["state"] == "refused"
            config.save(
                "grant",
                "herald",
                Grant(
                    read=[dest, private], listen=[dest, private], reply=[dest, private], post=[dest]
                ),
                expected_revision=2,
            )
            finish(routine)
            tick(2)
            assert worker.delivery.detail(intent["delivery_id"])["state"] == "confirmed"
            checks.append("routine_announcement")
            api["rate"] = True
            tick()
            calls = len(api["calls"])
            clock[0] += 1
            worker.step()
            assert len(api["calls"]) == calls
            clock[0] += 38
            tick()
            checks.append("rate_limit_no_early_network")
            for _ in range(55):
                api["add"](mentions=[])
            api["add"](author={"id": "500", "bot": True})
            api["add"](webhook_id="600")
            api["add"](type=6)
            api["add"](channel_id="301", author={"id": "401", "bot": False})
            api["add"](channel_id="999")
            mention = api["add"]()
            tick(8)
            turns = rows("chat_turns")
            assert len(turns) == 1 and turns[0]["message_id"] == mention
            reasons = {json.loads(r["receipt"]).get("reason") for r in rows("chat_inbound")}
            assert {
                "communications_unmentioned",
                "communications_nonhuman",
                "communications_sender_denied",
            } <= reasons, reasons
            assert any("before" in c[2] for c in api["calls"])
            assert not any("999" in c[1] for c in api["calls"])
            run = turns[0]["run_id"]
            app.state.executor.step()
            assert not runtime.call(
                run,
                "post-denied",
                POST,
                dict(operation_id="post-denied", destination=DEST, text="Denied"),
            )[0]
            finish(run)
            tick(3)
            assert rows("chat_turns")[0]["state"] == "closed"
            assert len(rows("tasks")) == 3
            checks.append("multipage_human_reply_and_loop_rejection")
            api["add"]()
            tick(2)
            run = next(t for t in rows("chat_turns") if t["state"] != "closed")["run_id"]
            app.state.executor.step()
            finish(run, known=False)
            api["lose"] = True
            tick(3)
            assert any(o["state"] == "unknown" for o in rows("delivery_operations"))
            sent = len(api["sent"])
            tick(3)
            assert len(api["sent"]) == sent
            checks.append("lost_ack_unknown_usage_no_resend")
        tables = (
            "runs",
            "chat_conversations",
            "communications_config",
            "communications_revisions",
            "communications_requests",
            "communications_calls",
            "run_communications",
            "notifications",
            "notification_forwarding",
            "notification_forwarding_origins",
            "chat_turns",
            "chat_inbound",
            "communications_cursors",
            "delivery_operations",
            "delivery_attempts",
        )
        before = {table: rows(table) for table in tables}
        with sqlite3.connect(data / "hearth.db") as db:
            db.execute("DROP TABLE notification_forwarding_origins")
            db.execute("ALTER TABLE notification_forwarding DROP COLUMN operator_url")
            db.execute("PRAGMA user_version=18")
        service.database.initialize()
        assert all(rows(table) == before[table] for table in tables)
        assert list(data.glob("*.before-v18*"))
        checks.append("populated_forward_upgrade")
        app, service, runtime = open_app()
        worker = Worker(service, secrets, {"discord": api["factory"]})
        with worker:
            tick(3)
            assert len(api["sent"]) == sent and len(rows("tasks")) == 4
            checks.append("restart_replay_no_resend")
            worker.forwarding.configure(
                "notices",
                dest,
                kinds=["run.succeeded"],
                enabled=True,
                expected_revision=0,
                operator_url="https://synthetic.example",
            )
            tick()
            assert len(api["sent"]) == sent
            notice = next(n for n in rows("notifications") if n["resource_id"] == operator)
            Inbox(service).mark(notice["id"], read=True)
            audit = next(
                a
                for a in rows("audit")
                if a["kind"] == "notification.recorded" and a["resource_id"] == notice["id"]
            )
            assert (
                worker.forwarding.enqueue(
                    "notices",
                    backfill_after=audit["sequence"] - 1,
                    through_cursor=audit["sequence"],
                    limit=1,
                )
                == 1
            )
            tick(3)
            assert len(api["sent"]) == sent + 1 and len(rows("tasks")) == 4
            assert (
                worker.forwarding.enqueue(
                    "notices",
                    backfill_after=audit["sequence"] - 1,
                    through_cursor=audit["sequence"],
                    limit=1,
                )
                == 0
            )
            operation = next(
                o
                for o in rows("delivery_operations")
                if json.loads(o["intent"])["kind"] == "notification"
            )
            detail = Inspection(worker).delivery(operation["id"])
            assert detail["run_id"] == operator, detail
            assert detail["run"]["usage_known"] and detail["run"]["actual_cost"] > 0
            assert detail["notification"]["read_at"] is not None
            checks.append("separate_notification_forwarding")
        before = {table: rows(table) for table in tables}
        capture(data, root / "backup")
        restore(root / "backup", root / "held")
        held = Hearth(Database(root / "held" / "hearth.db"))
        with held.database.transaction() as db:
            assert all(
                [dict(r) for r in db.execute(f"SELECT * FROM {table}")] == before[table]
                for table in tables
            )
        calls = len(api["calls"])
        try:
            with Worker(held, secrets, {"discord": api["factory"]}):
                raise AssertionError("held worker started")
        except Refused as error:
            assert error.code == "restored_copy_read_only"
        assert len(api["calls"]) == calls
        inert = Worker(held, secrets, {"discord": api["factory"]})
        for action in (
            inert.step,
            lambda: inert.probe("discord", "public"),
            lambda: inert.delivery.activate(
                "discord", expected_revision=1, operator_id="operator", old_consumer_stopped=True
            ),
        ):
            try:
                action()
                raise AssertionError("held mutation succeeded")
            except Refused as error:
                assert error.code == "restored_copy_read_only"
        assert len(api["calls"]) == calls
        assert not list((root / "held").glob("*.lock"))
        usage = Inspection(worker).usage()["origins"]
        assert {r["origin"] for r in usage} == {"operator", "routine", "conversation"}
        assert sum(r["unknown_runs"] for r in usage) == 1
        assert all(r["known_cost"] > 0 for r in usage if r["unknown_runs"] == 0)
        assert SECRET not in json.dumps(rows("audit"))
        checks.append("held_restore_preserves_evidence_no_io")
        return dict(
            synthetic_only=True,
            checks=checks,
            runs=len(rows("runs")),
            known_runs=sum(r["usage_known"] for r in rows("runs")),
            unknown_cost_runs=sum(not r["usage_known"] for r in rows("runs")),
            known_cost_microdollars=sum(r["actual_cost"] or 0 for r in rows("runs")),
            origins=sorted({r["origin"] for r in usage}),
            turns=len(rows("chat_turns")),
            deliveries=len(rows("delivery_operations")),
            unknown_deliveries=sum(o["state"] == "unknown" for o in rows("delivery_operations")),
            external_fixture_posts=len(api["sent"]),
            http_requests=len(api["calls"]),
        )
