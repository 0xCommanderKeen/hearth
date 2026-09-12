"""Installed-wheel mention, ordinary fake-runtime run, reply, restart and held copy."""

import json
import sys
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import hearth
from hearth.app import create_app
from hearth.channels.chat.config import Configuration, Secrets
from hearth.channels.chat.model import Address, Connection, Destination, Grant, Route
from hearth.channels.delivery.model import Receipt
from hearth.channels.interface import Message
from hearth.channels.polling import Page
from hearth.channels.worker import Worker
from hearth.residents.models import Declaration, Refused
from hearth.storage.backup import capture, restore
from hearth.storage.database import Database
from hearth.work.service import Hearth

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fake_native_runtime import FakeNativeRuntime  # noqa: E402

assert Path(hearth.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
state = {"messages": [], "sent": []}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/latest":
            result = str(max([int(m["message_id"]) for m in state["messages"]], default=0))
        elif self.path == "/poll":
            result = {
                "messages": [
                    m
                    for m in state["messages"]
                    if int(request["after"]) < int(m["message_id"]) <= int(request["through"])
                ],
                "complete": True,
            }
        else:
            state["sent"].append(request)
            result = {"external_id": "synthetic-reply"}
        encoded = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_):
        pass


class Adapter:
    def request(self, path, payload, *, max_bytes, timeout):
        with urlopen(
            Request(
                f"http://127.0.0.1:{server.server_port}/{path}", data=json.dumps(payload).encode()
            ),
            timeout=timeout,
        ) as response:
            raw = response.read(max_bytes + 1)
            assert len(raw) <= max_bytes
            return json.loads(raw)

    def latest(self, guild, channel, **limits):
        return self.request("latest", {}, **limits)

    def poll(self, guild, channel, *, after, through, limit, **limits):
        result = self.request("poll", {"after": after, "through": through}, **limits)
        assert len(result["messages"]) <= limit
        return Page(tuple(Message(**m) for m in result["messages"]), result["complete"])

    def send(self, permit, **limits):
        result = self.request("send", permit.model_dump(), **limits)
        return Receipt(
            attempt_id=permit.attempt_id,
            intent_sha256=permit.intent_sha256,
            outcome="confirmed",
            external_id=result["external_id"],
            evidence="loopback_verified",
        )

    def close(self):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever)
thread.start()
try:
    data = Path.cwd() / "communications-data"
    app = create_app(data, "synthetic-installed-token", supervise=False, runtime=FakeNativeRuntime)
    service = app.state.hearth
    service.save_resident(
        "herald", Declaration("Herald", "Answer bounded mentions", 10_000_000), expected_revision=0
    )
    root = Path.cwd() / "communications-secrets"
    root.mkdir(mode=0o700)
    (root / "bot").write_text("synthetic-only-bot-secret")
    (root / "bot").chmod(0o600)
    secrets = Secrets(root)
    config = Configuration(service, secrets)
    config.save(
        "connection",
        "bot",
        Connection(transport="discord", bot_id="account", secret_ref="bot", state="active"),
        expected_revision=0,
    )
    dest = Destination(connection_id="bot", guild_id="guild", channel_id="channel")
    config.save("grant", "herald", Grant(listen=[dest], reply=[dest]), expected_revision=0)
    config.save(
        "route",
        "route",
        Route(
            connection_id="bot",
            resident_id="herald",
            address=Address(guild_id="guild", channel_id="channel"),
            sender_policy="guild_channel_humans",
            state="active",
        ),
        expected_revision=0,
    )
    worker = Worker(service, secrets, {"discord": lambda *_: Adapter()})
    worker.delivery.activate(
        "bot", expected_revision=0, operator_id="operator", old_consumer_stopped=True
    )
    with worker:
        worker.step()
        state["messages"].append(
            asdict(
                Message(
                    "account",
                    "guild",
                    "channel",
                    "1",
                    "human",
                    int(time.time()),
                    "A synthetic mention",
                    True,
                    True,
                    False,
                    True,
                )
            )
        )
        deadline = time.monotonic() + 15
        while not state["sent"]:
            worker.step()
            app.state.executor.step()
            if time.monotonic() > deadline:
                raise RuntimeError("Installed communications journey did not finish")
            time.sleep(0.1)
        assert worker.delivery.inspect()[0]["state"] == "confirmed"
    with Worker(service, secrets, {"discord": lambda *_: Adapter()}) as restarted:
        restarted.step()
    assert len(state["sent"]) == 1
    with service.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM chat_turns").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM delivery_operations").fetchone()[0] == 1
    capture(data, Path.cwd() / "communications-backup")
    restore(Path.cwd() / "communications-backup", Path.cwd() / "communications-held")
    held = Hearth(Database(Path.cwd() / "communications-held/hearth.db"))
    try:
        with Worker(held, secrets, {"discord": lambda *_: Adapter()}):
            raise AssertionError("held worker started")
    except Refused as error:
        assert error.code == "restored_copy_read_only"
    print("Installed communications: one mention, run, reply; restart and held restore passed.")
finally:
    server.shutdown()
    thread.join()
    server.server_close()
