"""CLI uses authenticated bounded HTTP; explicit probes reach the server owner."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import uvicorn
from hearth.app import create_app
from hearth.channels.chat.config import Secrets
from hearth.channels.worker import Worker
from hearth.residents.models import Declaration

from tests.fake_runtime import fake_runtime


@pytest.fixture
def server():
    state = {"requests": [], "status": 200, "result": {"items": [], "next_after": None}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.answer()

        def do_POST(self):
            self.answer()

        def answer(self):
            length = int(self.headers.get("Content-Length", "0"))
            state["requests"].append(
                (
                    self.command,
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(length),
                )
            )
            raw = state.get("raw", json.dumps(state["result"]).encode())
            self.send_response(state["status"])
            if state["status"] == 302:
                self.send_header("Location", state["url"] + "/do-not-follow")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["url"] = f"http://127.0.0.1:{http.server_port}"
    thread = threading.Thread(target=http.serve_forever)
    thread.start()
    yield state
    http.shutdown()
    thread.join()
    http.server_close()


def run(server, *args, token="synthetic-cli-operator-token"):
    return subprocess.run(
        [sys.executable, "-m", "hearth", "communications", *args, "--url", server["url"]],
        env={**os.environ, "HEARTH_OPERATOR_TOKEN": token},
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_lists_and_inspection_keep_bounded_explicit_sections(server):
    result = run(
        server,
        "list",
        "--section",
        "deliveries",
        "--kind",
        "notification",
        "--limit",
        "2",
        "--offset",
        "3",
    )
    assert result.returncode == 0 and json.loads(result.stdout) == server["result"]
    method, path, auth, _ = server["requests"][-1]
    assert (
        method == "GET"
        and path == "/api/communications/deliveries?limit=2&offset=3&kind=notification"
    )
    assert auth == "Bearer synthetic-cli-operator-token"
    server["result"] = {"text": "explicit bounded retained text"}
    assert (
        json.loads(run(server, "inspect", "--section", "conversations", "--id", "turn").stdout)
        == server["result"]
    )
    assert server["requests"][-1][1] == "/api/communications/conversations/turn?limit=30"
    before = len(server["requests"])
    result = run(server, "list", "--limit", "101")
    assert result.returncode != 0 and "communications_page_invalid" in result.stderr
    assert len(server["requests"]) == before


def test_probe_is_explicit_authenticated_post_not_local_worker(server):
    result = run(server, "probe", "--id", "bot", "--route", "route")
    assert result.returncode == 0
    method, path, _, body = server["requests"][-1]
    assert (method, path) == ("POST", "/api/communications/connections/bot/probe")
    assert json.loads(body) == {"route_id": "route"}
    assert run(server, "probe", "--id", "bot").returncode != 0


@pytest.mark.parametrize("status", [401, 409, 500, 302])
def test_error_bodies_tokens_and_redirects_never_escape_cli(server, status):
    server.update(status=status, raw=b"raw secret credential path /private/LEAK_ME")
    result = run(server, "list")
    assert result.returncode != 0 and "LEAK_ME" not in result.stderr
    assert "synthetic-cli-operator-token" not in result.stderr
    assert len(server["requests"]) == 1


def test_response_bound_and_missing_auth(server):
    assert run(server, "list", token="").returncode != 0
    assert server["requests"] == []
    server["raw"] = b"x" * 1_000_001
    result = run(server, "list")
    assert "communications_response_too_large" in result.stderr
    assert len(result.stderr) < 3000


def test_cli_setup_reload_revision_and_revoke_use_real_owner(tmp_path):
    root = tmp_path / "secrets"
    root.mkdir(mode=0o700)
    app = create_app(
        tmp_path / "data",
        "synthetic-cli-operator-token",
        supervise=False,
        runtime=fake_runtime(),
        communications=lambda hearth: Worker(hearth, Secrets(root), {}),
    )
    app.state.hearth.save_resident(
        "herald", Declaration("Herald", "Synthetic setup", 10_000_000), expected_revision=0
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    owner = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: owner.run(sockets=[listener]))
    thread.start()
    server = {"url": f"http://127.0.0.1:{listener.getsockname()[1]}"}
    try:
        deadline = time.monotonic() + 5
        while not owner.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        destination = {"connection_id": "bot", "guild_id": "100", "channel_id": "200"}
        values = [
            ("connection", "bot", {"transport": "discord", "bot_id": "300", "secret_ref": "bot"}),
            (
                "route",
                "room",
                {
                    "connection_id": "bot",
                    "resident_id": "herald",
                    "address": {"guild_id": "100", "channel_id": "200"},
                    "sender_policy": "guild_channel_humans",
                },
            ),
            (
                "grant",
                "herald",
                {
                    "read": [destination],
                    "listen": [destination],
                    "reply": [destination],
                    "post": [],
                },
            ),
        ]
        for kind, identity, value in values:
            source = tmp_path / f"{kind}.json"
            source.write_text(json.dumps(value))
            args = (
                "save",
                "--kind",
                kind,
                "--id",
                identity,
                "--expected-revision",
                "0",
                "--file",
                str(source),
            )
            result = run(server, *args)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {"id": identity, "revision": 1}
            assert run(server, *args).returncode != 0
        result = run(server, "list", "--section", "configuration")
        config = json.loads(result.stdout)
        assert len(config["configuration"]) == 3
        assert config["health"]["communications"] == "stopped"
        assert str(root) not in result.stdout
        assert run(server, "activate", "--id", "bot", "--expected-revision", "0").returncode != 0
        (root / "bot").write_text("synthetic-local-bot-token")
        (root / "bot").chmod(0o600)
        source = tmp_path / "connection.json"
        source.write_text(json.dumps(values[0][2] | {"state": "active"}))
        result = run(
            server,
            "save",
            "--kind",
            "connection",
            "--id",
            "bot",
            "--expected-revision",
            "1",
            "--file",
            str(source),
        )
        assert result.returncode == 0, result.stderr
        result = run(
            server,
            "activate",
            "--id",
            "bot",
            "--expected-revision",
            "0",
            "--old-consumer-stopped",
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"connection_id": "bot", "revision": 1}
        for kind, identity, _ in reversed(values):
            result = run(
                server,
                "revoke",
                "--kind",
                kind,
                "--id",
                identity,
                "--expected-revision",
                "2" if kind == "connection" else "1",
            )
            assert result.returncode == 0, result.stderr
        config = json.loads(run(server, "list", "--section", "configuration").stdout)
        for item in config["configuration"]:
            assert item["value"]["revision"] == (3 if item["kind"] == "connection" else 2)
            if item["kind"] == "grant":
                assert all(
                    item["value"][power] == [] for power in ("read", "listen", "reply", "post")
                )
            else:
                assert item["value"]["state"] == "disabled"
        source = tmp_path / "invalid.json"
        source.write_text('{"token":"NEVER_ECHO_THIS"}')
        result = run(
            server,
            "save",
            "--kind",
            "connection",
            "--id",
            "other",
            "--expected-revision",
            "0",
            "--file",
            str(source),
        )
        assert result.returncode != 0 and "NEVER_ECHO_THIS" not in result.stderr
        assert "synthetic-cli-operator-token" not in result.stderr
        assert "synthetic-local-bot-token" not in json.dumps(config)
    finally:
        owner.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize(
    "origin", ["http://[", "http://localhost:bad", "http://localhost/\nLEAK_ME"]
)
def test_malformed_server_reference_is_sanitized_before_request(server, origin):
    server["url"] = origin
    result = run(server, "list")
    assert result.returncode != 0
    assert "communications_server_invalid" in result.stderr
    assert "Traceback" not in result.stderr and "LEAK_ME" not in result.stderr
    assert server["requests"] == []
