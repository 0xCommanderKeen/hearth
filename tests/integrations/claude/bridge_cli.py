"""A scripted `claude` that really speaks MCP to Hearth's own shim.

The fake in `test_claude_live.py` only replays a recorded stream. This one is the
other half of the bridge: it reads the `--mcp-config` Hearth wrote, launches the shim
named there with exactly the environment Hearth declared for it, performs the MCP
handshake over stdio, lists the tools, makes the calls its script asks for, and only
then writes the session's stream. So every test that uses it exercises the real shim,
the real unix socket and the real trusted worker, and never reaches Anthropic.

It is not imported by the code under test: the fake `claude` executable adds this
directory to `sys.path` and calls `main`. Nothing here imports Hearth.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

VERSION = "2.1.263 (Claude Code)"
BUILD = "2.1.263"


def read_flag(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


class Shim:
    """The other end of one stdio MCP connection, spoken by hand."""

    def __init__(self, command, arguments, environment):
        self.child = subprocess.Popen(
            [command, *arguments],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.sequence = 0

    def write(self, raw: bytes):
        assert self.child.stdin is not None
        self.child.stdin.write(raw)
        self.child.stdin.flush()

    def read(self):
        assert self.child.stdout is not None
        line = self.child.stdout.readline()
        return json.loads(line) if line.strip() else None

    def request(self, method, params=None, ident=None):
        if ident is None:
            self.sequence += 1
            ident = self.sequence
        message = {"jsonrpc": "2.0", "id": ident, "method": method}
        if params is not None:
            message["params"] = params
        self.write(json.dumps(message).encode() + b"\n")
        return self.read()

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.write(json.dumps(message).encode() + b"\n")

    def close(self):
        if self.child.stdin is not None:
            self.child.stdin.close()
        self.child.wait(timeout=10)


def main(session_path, argv) -> int:
    if "--version" in argv:
        print(VERSION)
        return 0
    if argv[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
        return 0
    session = json.loads(Path(session_path).read_text())
    record = {"argv": argv, "prompt": sys.stdin.read(), "calls": []}
    tools = [name for name in (read_flag(argv, "--tools") or "").split(",") if name]
    allowed = [name for name in (read_flag(argv, "--allowedTools") or "").split(",") if name]
    record["tools"] = tools
    record["allowed"] = allowed
    configuration = read_flag(argv, "--mcp-config")
    shim = None
    if configuration is not None:
        record["configuration"] = json.loads(Path(configuration).read_text())
        # The measured CLI connects to its MCP servers and lists their tools before it
        # reports anything, because the `init` event names the tools it found.
        shim = _connect(record, session)
    try:
        early = session.get("steps_before_init", False)
        if shim is not None and early:
            _steps(record, shim, session, retry=False)
        _publish(record, session)
        _stream(record, session, argv, tools, shim)
    finally:
        if shim is not None:
            shim.close()
    return int(session.get("exit_code", 0))


def _connect(record, session):
    """Launch the shim with exactly the command and environment Hearth declared."""
    server = record["configuration"]["mcpServers"]["hearth"]
    command = server["command"]
    if not Path(command).exists():
        # A sandboxed session is told to start the shim with the *image's* own
        # interpreter, which is where Hearth's package sits inside the sandbox. The
        # fake daemon has no namespace to provide one (`tests/fake_docker.py` runs the
        # command on this host), so the interpreter here is this test's own -- and the
        # path Hearth wrote is asserted where it is written, not silently accepted.
        command = sys.executable
    shim = Shim(command, server["args"], dict(server.get("env", {})))
    record["shim_environment"] = server.get("env")
    record["initialize"] = shim.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "claude-code", "version": BUILD},
        },
    )
    shim.notify("notifications/initialized")
    record["listed"] = shim.request("tools/list", {})
    return shim


def _steps(record, shim, session, *, retry: bool):
    for step in session.get("steps", []):
        _step(record, shim, step, retry=retry and not session.get("no_retry", False))


def _untrusted(reply) -> bool:
    result = (reply or {}).get("result") or {}
    return result.get("isError") is True and any(
        "management_session_untrusted" in item.get("text", "") for item in result.get("content", [])
    )


def _step(record, shim, step, *, retry: bool = True):
    if "await" in step:
        deadline = time.monotonic() + step.get("timeout", 20)
        while time.monotonic() < deadline and not Path(step["await"]).exists():
            time.sleep(0.02)
        return
    if "signal" in step:
        Path(step["signal"]).write_text("go")
        return
    if "raw" in step:
        # A frame the CLI would never send. The shim has to answer it and stay up.
        shim.write(step["raw"].encode() + b"\n")
        record["calls"].append({"raw": step["raw"], "reply": shim.read()})
        return
    if "method" in step:
        record["calls"].append(
            {"method": step["method"], "reply": shim.request(step["method"], step.get("params"))}
        )
        return
    params = {"name": step["tool"], "arguments": step.get("arguments", {})}
    reply = shim.request("tools/call", params, ident=step.get("id"))
    # Hearth opens the bridge when it has read the `init` event this session already
    # wrote; the real CLI does not have to wait for that, because its first model turn
    # is far behind its own output. A script that means to call early says so.
    deadline = time.monotonic() + 10
    while retry and _untrusted(reply) and time.monotonic() < deadline:
        time.sleep(0.05)
        reply = shim.request("tools/call", params, ident=step.get("id"))
    record["calls"].append({"tool": step["tool"], "reply": reply})


def _publish(record, session):
    Path(session["record"]).write_text(json.dumps(record, indent=2, sort_keys=True))


def _answers(record) -> list[str]:
    """What the tools said, as the session's own answer would repeat it."""
    texts = []
    for call in record["calls"]:
        result = (call.get("reply") or {}).get("result") or {}
        for item in result.get("content", []):
            texts.append(item.get("text", ""))
    return texts


def _stream(record, session, argv, tools, shim):
    """The session's own stream-json, with the recorded fixture's numbers."""
    lines = [
        line
        for line in Path(session["fixture"]).read_text().splitlines()
        if line.strip() and json.loads(line).get("subtype") != "init"
    ]
    init = {
        "type": "system",
        "subtype": "init",
        "cwd": os.getcwd(),
        "session_id": session.get("session_id", "11111111-1111-4111-8111-111111111111"),
        "tools": session.get("reported_tools", tools),
        "mcp_servers": session.get(
            "reported_servers", [{"name": "hearth", "status": "connected"}] if tools else []
        ),
        "model": read_flag(argv, "--model", "claude-opus-5"),
        "permissionMode": "default",
        "slash_commands": [],
        "apiKeySource": "none",
        "claude_code_version": session.get("reported_version", BUILD),
        "output_style": "default",
        "uuid": session.get("uuid", "22222222-2222-4222-8222-222222222222"),
    }
    sys.stdout.write(json.dumps(init) + "\n")
    sys.stdout.flush()
    if shim is not None and not session.get("steps_before_init", False):
        # The turn itself: every tool call happens after the session has reported what
        # it connected to, which is the only order the real CLI can produce.
        _steps(record, shim, session, retry=True)
        _publish(record, session)
    answer = session.get("answer_prefix", "") + "\n".join(_answers(record))
    for line in lines:
        event = json.loads(line)
        if event.get("type") == "result" and answer.strip():
            event["result"] = answer
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()
        time.sleep(session.get("pause", 0))
