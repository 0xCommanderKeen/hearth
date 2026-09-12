"""CLI uses authenticated bounded HTTP; explicit probes reach the server owner."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


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
