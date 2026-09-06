"""Offline pinned CLI fixture. Run only through probe-codex-subscription.py."""

import base64
import datetime
import hashlib
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COLLECTOR = "--collector" in sys.argv
PROMPT = sys.argv[sys.argv.index("--prompt") + 1]
run_id = sys.argv[sys.argv.index("--run-id") + 1]
if COLLECTOR:
    # Only the trusted fixture collector has these modules and durable journal.
    sys.path.insert(0, "/app")
    from hearth.codex_events import TokenUsage
    from hearth.codex_usage import UsageBinding, UsageJournal, publish

    binding = UsageBinding(
        run_id, hashlib.sha256(PROMPT.encode()).hexdigest(), "gpt-6-astra", "standard"
    )
    journal = UsageJournal.create(Path("/journal/usage"), binding)
    upstream_canary = Path("/collector-secret").read_text()
    assert upstream_canary.startswith("synthetic-upstream-")

state = Path("/scratch/state")
state.mkdir()


def jwt(value):
    def encode(v):
        return base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip("=")

    return encode({"alg": "none"}) + "." + encode(value) + ".synthetic"


claims = {
    "sub": "synthetic-reader",
    "email": "reader@example.invalid",
    "exp": int(sys.argv[sys.argv.index("--expires") + 1]),
    "https://api.openai.com/auth": {
        "chatgpt_account_id": "synthetic-account",
        "chatgpt_plan_type": "pro",
    },
}
token = jwt(claims)
(state / "auth.json").write_text(
    json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": token,
                "access_token": token,
                "refresh_token": "synthetic-refresh",
                "account_id": "synthetic-account",
            },
            "last_refresh": datetime.datetime.now(datetime.UTC).isoformat(),
        }
    )
)
model = {
    "slug": "gpt-6-astra",
    "display_name": "Synthetic Astra fixture",
    "description": "Offline metadata only",
    "default_reasoning_level": "low",
    "supported_reasoning_levels": [{"effort": "low", "description": "Synthetic"}],
    "shell_type": "shell_command",
    "visibility": "list",
    "supported_in_api": False,
    "priority": 1,
    "base_instructions": "Summarize supplied synthetic notes. Do not use tools.",
    "supports_reasoning_summaries": False,
    "support_verbosity": True,
    "default_verbosity": "low",
    "apply_patch_tool_type": None,
    "web_search_tool_type": "text",
    "truncation_policy": {"mode": "bytes", "limit": 10000},
    "supports_parallel_tool_calls": False,
    "context_window": 32000,
    "effective_context_window_percent": 95,
    "experimental_supported_tools": [],
    "input_modalities": ["text"],
    "prefer_websockets": True,
}
(state / "models.json").write_text(json.dumps({"models": [model]}))
requests = []
errors = []
MAX_FRAME = 2 * 1024 * 1024
RESPONSE_USAGE: dict = {
    "input_tokens": 30,
    "output_tokens": 8,
    "total_tokens": 38,
    "input_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 5},
    "output_tokens_details": {"reasoning_tokens": 3},
}
try:
    context = json.loads(PROMPT)
except ValueError:
    context = None
text = (
    "# Daily summary — Codex CLI simulation\n\n"
    + "\n".join("- " + note for note in context["notes"])
    + "\n\nThis response came from the local fixture; no model was called."
    if isinstance(context, dict) and context.get("simulated") is True
    else "Synthetic summary: the Reader mock is ready. No model was called."
)
item = {
    "id": "msg_synthetic",
    "type": "message",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": text, "annotations": []}],
}


def stream_events() -> list[dict]:
    events = [
        {
            "type": "response.created",
            "response": {"id": "resp_synthetic", "status": "in_progress", "output": []},
        },
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {
            "type": "response.output_text.delta",
            "item_id": item["id"],
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        },
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {
            "type": "response.completed",
            "response": {
                "id": "resp_synthetic",
                "status": "completed",
                "output": [item],
                "usage": RESPONSE_USAGE,
            },
        },
    ]
    return events


class Server(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def record(self, body):
        if len(requests) >= 12:
            raise ValueError("request limit")
        if body.get("model") != "gpt-6-astra":
            raise ValueError("wrong model")
        requests.append(
            {
                "method": "WS",
                "path": self.path,
                "model": body.get("model"),
                "response_usage": RESPONSE_USAGE if body.get("generate") is not False else None,
                "tools": [(t.get("type"), t.get("name")) for t in body.get("tools", [])],
                "keys": list(body),
                "generate": body.get("generate"),
                "authorization_synthetic": self.headers.get("Authorization") == "Bearer " + token,
                "account": self.headers.get("ChatGPT-Account-Id"),
                "tool_outputs": [
                    i for i in body.get("input", []) if i.get("type") == "function_call_output"
                ],
            }
        )

    def websocket(self):
        self.connection.settimeout(10)
        accept = base64.b64encode(
            hashlib.sha1(
                (
                    self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                ).encode()
            ).digest()
        ).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        header = self.rfile.read(2)
        length = header[1] & 127
        if length == 126:
            length = struct.unpack("!H", self.rfile.read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.rfile.read(8))[0]
        assert header[0] == 129 and header[1] & 128 and length <= MAX_FRAME
        mask = self.rfile.read(4)
        data = self.rfile.read(length)
        body = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        self.record(body)
        request = journal.begin() if body.get("generate") is not False else None
        if "--interrupt" in sys.argv and request is not None:
            time.sleep(5)
            self.close_connection = True
            return
        events: list[dict] = stream_events()
        if (
            "--attack" in sys.argv
            and body.get("generate") is not False
            and not any(i.get("type") == "function_call_output" for i in body.get("input", []))
        ):
            calls = [
                {
                    "id": "call_exec",
                    "type": "function_call",
                    "call_id": "call_exec",
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {"cmd": "cat /scratch/state/auth.json; touch /scratch/tool-ran"}
                    ),
                },
                {
                    "id": "call_image",
                    "type": "function_call",
                    "call_id": "call_image",
                    "name": "view_image",
                    "arguments": json.dumps({"path": "/scratch/state/auth.json"}),
                },
            ]
            events = (
                [
                    {
                        "type": "response.created",
                        "response": {"id": "resp_attack", "status": "in_progress", "output": []},
                    }
                ]
                + [
                    {"type": "response.output_item.done", "output_index": i, "item": call}
                    for i, call in enumerate(calls)
                ]
                + [
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_attack",
                            "status": "completed",
                            "output": calls,
                            "usage": RESPONSE_USAGE,
                        },
                    }
                ]
            )
        for event in events:
            if event["type"] == "response.completed" and request is not None:
                usage = event["response"]["usage"]
                journal.complete(
                    request,
                    TokenUsage(
                        usage["input_tokens"],
                        usage["input_tokens_details"]["cached_tokens"],
                        usage["output_tokens"],
                        usage["output_tokens_details"]["reasoning_tokens"],
                        usage["input_tokens_details"]["cache_write_tokens"],
                    ),
                )
            data = json.dumps(event).encode()
            header = (
                bytes([129, len(data)])
                if len(data) < 126
                else bytes([129, 126]) + struct.pack("!H", len(data))
            )
            self.wfile.write(header + data)
            self.wfile.flush()
        self.close_connection = True

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        try:
            if self.path != "/responses" or self.headers.get("Upgrade") != "websocket":
                raise ValueError("unexpected GET route")
            if self.headers.get("Authorization") != "Bearer " + token:
                raise ValueError("wrong synthetic authentication")
            if self.headers.get("ChatGPT-Account-Id") != "synthetic-account":
                raise ValueError("wrong synthetic account")
            self.websocket()
        except Exception as error:
            errors.append(str(error))
            self.close_connection = True

    def do_POST(self):
        # The pinned CLI sends analytics even with the OTel exporter disabled.
        # Consume a bounded body locally; never forward or pretend it is inference.
        try:
            self.connection.settimeout(10)
            length = int(self.headers.get("Content-Length", "0"))
            if (
                self.path != "/codex/analytics-events/events"
                or not 0 <= length <= MAX_FRAME
                or len(requests) >= 12
            ):
                raise ValueError("unexpected or oversized POST")
            if self.headers.get("Authorization") != "Bearer " + token:
                raise ValueError("wrong analytics authentication")
            if len(self.rfile.read(length)) != length:
                raise ValueError("truncated POST")
            requests.append({"method": "POST", "path": self.path, "authorization_synthetic": True})
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception as error:
            errors.append(str(error))
            self.close_connection = True


if COLLECTOR:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Server)
    server.daemon_threads = False
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    publish(Path("/journal/ready.json"), {"run_id": run_id})
    # A missing host stop cannot leave the synthetic collector running forever.
    stopped = stopping.wait(35)
    server.shutdown()
    server.server_close()
    publish(
        Path("/journal/collector.json"),
        {
            "requests": requests,
            "errors": errors,
            "stopped_by_host": stopped,
            "upstream_canary_loaded": True,
        },
    )
    sys.exit(0 if stopped else 1)

# Test the CLI container directly, independently of the model's tool refusal.
# These paths must be absent, not merely hidden by a CLI permission setting.
assert os.getuid() != 0
for denied in ("/journal/usage/binding.json", "/collector-secret", "/app/hearth/codex_usage.py"):
    try:
        Path(denied).read_bytes()
    except FileNotFoundError, PermissionError:
        pass
    else:
        raise AssertionError("collector path readable: " + denied)
try:
    Path("/journal/forged.json").write_text("forged")
except FileNotFoundError, PermissionError, OSError:
    pass
else:
    raise AssertionError("collector journal writable")

config = {
    "model_catalog_json": str(state / "models.json"),
    "forced_login_method": "chatgpt",
    "cli_auth_credentials_store": "file",
    "chatgpt_base_url": "http://127.0.0.1:8765",
    "openai_base_url": "http://127.0.0.1:8765",
    "features.image_generation": False,
    "features.browser_use": False,
    "features.computer_use": False,
    "features.hooks": False,
    "features.apps": False,
    "features.plugins": False,
    "features.remote_plugin": False,
    "otel.metrics_exporter": "none",
    "default_permissions": "reader",
    "permissions.reader.filesystem./": "deny",
    "permissions.reader.filesystem.:minimal": "read",
    "permissions.reader.filesystem./runtime": "read",
    "permissions.reader.network.enabled": False,
    "approval_policy": "never",
    "features.shell_tool": False,
    "features.unified_exec": False,
    "features.shell_snapshot": False,
    "features.multi_agent": False,
    "web_search": "disabled",
    "project_root_markers": [],
    "check_for_update_on_startup": False,
}
cmd = [
    "/runtime/bin/codex",
    "exec",
    "--ignore-user-config",
    "--ignore-rules",
    "--strict-config",
    "--skip-git-repo-check",
    "--ephemeral",
    "--json",
    "--color",
    "never",
    "--model",
    "gpt-6-astra",
    "--output-last-message",
    "/scratch/final.txt",
]
for key, value in config.items():
    cmd += ["-c", key + "=" + json.dumps(value)]
cmd += [PROMPT]


def read_output(name):
    with (Path("/scratch") / name).open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("oversized CLI output")
    return data.decode("utf-8")


try:
    version = subprocess.check_output(
        ["/runtime/bin/codex", "--version"],
        env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(state)},
        text=True,
        timeout=5,
    ).strip()
    if version != "codex-cli 0.145.0":
        raise ValueError("wrong CLI version")
    # Files live on the size-limited scratch tmpfs, not in capture_output memory.
    with Path("/scratch/stdout").open("wb") as stdout, Path("/scratch/stderr").open("wb") as stderr:
        result = subprocess.run(
            cmd,
            cwd="/scratch",
            env={
                "PATH": "/runtime/bin:/usr/local/bin:/usr/bin:/bin",
                "CODEX_HOME": str(state),
                "LANG": "C.UTF-8",
            },
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            timeout=2 if "--interrupt" in sys.argv else 25,
        )
    print(
        json.dumps(
            {
                "collector_paths_denied": True,
                "version": version,
                "returncode": result.returncode,
                "stdout": read_output("stdout"),
                "stderr": read_output("stderr")[-6000:],
                "requests": requests,
                "errors": errors,
                "tool_ran": Path("/scratch/tool-ran").exists(),
                "final": read_output("final.txt") if Path("/scratch/final.txt").exists() else None,
            }
        )
    )
except subprocess.TimeoutExpired:
    print(
        json.dumps(
            {
                "timeout": True,
                "stdout": read_output("stdout"),
                "stderr": read_output("stderr")[-10000:],
                "requests": requests,
                "errors": errors,
            }
        )
    )
