"""Hearth's own tools inside a Claude session, with the authority left outside it.

Two halves live in this one file, and the split between them is the point.

**The shim** is what the CLI actually launches: `python -I -m
hearth.integrations.claude.mcp_bridge <socket path>`, named by the per-run
`--mcp-config`. It speaks the stdio half of MCP -- `initialize`, `tools/list`,
`tools/call` and `ping`, nothing else -- and answers none of it itself. Every request
that needs an answer is forwarded verbatim over a unix socket in the run's own folder,
and the reply is handed back. It opens no database, holds no owner token, reads no
credential and needs no environment beyond a `PATH`, so a session that got at it would
find nothing in it worth taking. It also keeps no connection between calls: one
request, one connection, closed either way, so a malformed frame costs that call and
never the run.

**The socket server** is the trusted half, and it runs inside the worker that owns the
run. It answers `tools/list` from the tool list the run was admitted with, and every
`tools/call` through `management.bridge`, which authenticates with `BoundRun` and
mutates and audits in one transaction -- exactly what `codex/management_runtime.py`
does over the app-server's `dynamicTools`. The socket sits in a directory only the
worker's user can enter, is created 0600, and every connection's peer credentials are
checked to be that same user before a byte of it is read.

**Across the sandbox boundary the split is the same and so is the check.** The shim
runs inside the run's container, started by the CLI from the image's own interpreter,
and the socket is bind-mounted into it at the path Hearth wrote -- which holds on a
Linux host and not on a Mac, where a socket file cannot be bind-mounted at all
(`docs/sandbox.md`, measurement 6). The trusted half never crosses: it stays in the
worker, on the host, with the store. What the peer check then compares is a uid on
either side of a namespace, so it means what it says only while the two agree on the
number: the sandbox runs `--user <Hearth's uid>` and the container must not be started
in a user namespace that remaps it (`docker run --userns=host` is the default; a daemon
in `userns-remap` mode would make the session's uid something else, and the bridge
would refuse every call rather than answer the wrong process).

Nothing here decides authority. The offered tool list, the call limit and every
refusal come from `management.bridge.authorize`, so a grant revoked mid-run is felt on
the next call, and a run pinned without a grant (ADR 0012) reaches its own memory and
journal and nothing else.
"""

import contextlib
import json
import os
import re
import selectors
import socket
import sqlite3
import struct
import sys
from pathlib import Path

# The MCP server name the CLI knows Hearth by, and the prefix it gives every tool of
# that server. `--tools` and `--allowedTools` are written in these names; the wire and
# the database only ever see Hearth's own `hearth_*` names. A tool keeps that name
# unchanged rather than being stripped for the prefix's sake -- `hearth_journal_write`
# is `mcp__hearth__hearth_journal_write` to the CLI -- so the name in the argv, in the
# session's `init` event, on the socket, in `management_calls` and in the audit is one
# name, mapped by nothing that could map it wrongly.
SERVER_NAME = "hearth"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
# Named rather than taken from `__name__`: the shim runs this file as `__main__`.
MODULE = "hearth.integrations.claude.mcp_bridge"
# The revision of MCP this shim implements. A client asking for another revision is
# answered in the one it asked for, as the specification's negotiation expects.
PROTOCOL_VERSION = "2025-06-18"
SOCKET_NAME = "bridge.sock"
CONFIG_NAME = "mcp.json"
# One request frame. `hearth_residents_configure` carries up to 1.5 MB of arguments,
# which `management.bridge` bounds for itself; this only has to be wider than that.
MAX_FRAME = 2 * 1024 * 1024
# How long the shim waits for the trusted half to answer one call. A call is a single
# write transaction, but it can queue behind another writer.
CALL_TIMEOUT = 600.0
# `sun_path` is 104 bytes on macOS and 108 on Linux. A run folder under a deep
# temporary path does not fit, so a long address is bound and connected relative to
# its own directory instead: the same socket file, named more briefly.
SUN_PATH_LIMIT = 100
# How many connections the trusted half will hold at once. The shim uses one per call
# and closes it, so this is only ever reached by something that stopped speaking.
MAX_CONNECTIONS = 16
# What one tool call may raise out of Hearth's own writer. None of them is the model's
# doing, and each one ends the session rather than being answered as a refusal.
BRIDGE_FAILURES = (OSError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error)


def socket_path(folder: Path) -> Path:
    return folder / SOCKET_NAME


def configuration_path(folder: Path) -> Path:
    return folder / CONFIG_NAME


def _address(path) -> tuple[str, str | None]:
    """How to name this socket to the kernel, and where to stand while doing it."""
    text = os.fspath(path)
    if len(os.fsencode(text)) < SUN_PATH_LIMIT:
        return text, None
    return os.path.basename(text), os.path.dirname(text)


@contextlib.contextmanager
def _at(directory: str | None):
    if directory is None:
        yield
        return
    previous = os.getcwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


def connect(path, timeout: float = CALL_TIMEOUT) -> socket.socket:
    address, directory = _address(path)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout)
        with _at(directory):
            client.connect(address)
    except BaseException:
        client.close()
        raise
    return client


def listen(path, backlog: int = 8) -> socket.socket:
    """Bind the run's own socket: only this user reaches it, and only this process."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        address, directory = _address(path)
        with _at(directory):
            server.bind(address)
        # The containing folder is already 0700; the socket says the same on its own.
        os.chmod(path, 0o600)
        server.listen(backlog)
        server.setblocking(False)
    except BaseException:
        server.close()
        raise
    return server


def peer_uid(connection: socket.socket) -> int | None:
    """Who is on the other end, from the kernel rather than from what they claim."""
    if hasattr(socket, "SO_PEERCRED"):
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", raw)[1]
    peercred = getattr(socket, "LOCAL_PEERCRED", None)
    if peercred is None:
        # Neither credential interface exists here, so no peer can be proven local.
        return None
    # macOS answers with a `struct xucred`: a version, the uid, then group membership.
    version, uid = struct.unpack_from("<II", connection.getsockopt(0, peercred, 128))
    return uid if version == 0 else None


# ---------------------------------------------------------------------------
# The shim: no database, no token, no authority. It forwards, and it answers.
# ---------------------------------------------------------------------------


def _reply(ident, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _failure(ident, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def ask(path, request: dict) -> dict | None:
    """One request over one connection, then let it go. None means nobody answered."""
    try:
        with connect(path) as client:
            client.sendall(json.dumps(request).encode() + b"\n")
            # The trusted half reads one frame; saying the request is complete lets it
            # answer without waiting for a length nobody sent.
            client.shutdown(socket.SHUT_WR)
            buffer = bytearray()
            while b"\n" not in buffer:
                chunk = client.recv(65536)
                if not chunk:
                    return None
                buffer.extend(chunk)
                if len(buffer) > MAX_FRAME:
                    return None
            answer = json.loads(bytes(buffer).split(b"\n", 1)[0])
    except OSError, ValueError, RecursionError:
        return None
    return answer if isinstance(answer, dict) else None


def respond(path, line: bytes) -> dict | None:
    """One JSON-RPC message in, at most one message out. This never raises."""
    try:
        message = json.loads(line)
    except ValueError, RecursionError:
        return _failure(None, -32700, "invalid JSON")
    if not isinstance(message, dict):
        return _failure(None, -32600, "invalid request")
    ident, method = message.get("id"), message.get("method")
    # A notification (no id) and a response to something this shim never asked are
    # both nothing to answer; silence is the protocol's own reply to them.
    if ident is None or not isinstance(method, str) or not isinstance(ident, int | str):
        return None
    if method == "initialize":
        params = message.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return _reply(
            ident,
            {
                "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
            },
        )
    if method == "ping":
        return _reply(ident, {})
    if method == "tools/list":
        forwarded = ask(path, {"op": "tools/list"})
    elif method == "tools/call":
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        name, arguments = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _failure(ident, -32602, "invalid tool call")
        # The call is named by the request the CLI made, so a frame replayed under an
        # identity already used is refused by the trusted half rather than applied
        # a second time.
        forwarded = ask(
            path,
            {"op": "tools/call", "call_id": str(ident), "tool": name, "arguments": arguments},
        )
    else:
        return _failure(ident, -32601, f"unsupported method: {method}")
    if forwarded is None or not isinstance(forwarded.get("result"), dict):
        return _failure(ident, -32603, "the Hearth bridge did not answer")
    return _reply(ident, forwarded["result"])


def _write(output, message: dict) -> None:
    output.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
    output.flush()


def serve_stdio(path, stream, output) -> int:
    """Read line-delimited JSON-RPC until the client stops speaking."""
    try:
        while True:
            line = stream.readline(MAX_FRAME + 1)
            if not line:
                return 0
            if not line.endswith(b"\n"):
                if len(line) <= MAX_FRAME:
                    # End of input without a newline: answer what is there, then stop.
                    answered = respond(path, line)
                    if answered is not None:
                        _write(output, answered)
                    return 0
                # Drop the rest of an oversized frame rather than the session: the next
                # well-formed request still gets its answer.
                while True:
                    rest = stream.readline(MAX_FRAME + 1)
                    if not rest or rest.endswith(b"\n"):
                        break
                _write(output, _failure(None, -32600, "request too large"))
                continue
            answered = respond(path, line)
            if answered is not None:
                _write(output, answered)
    except OSError:
        # The session went away mid-answer. That is the CLI's ending, not an error
        # of this shim's, and it says nothing about it on the way out.
        return 1


# ---------------------------------------------------------------------------
# The trusted half: the worker's own socket, its own transaction, its own authority.
# ---------------------------------------------------------------------------

_TOOL_NAME = re.compile(r"hearth_[a-z][a-z0-9_]{0,63}")


def checked_tools(tools) -> list[dict]:
    """The tool list Hearth built for one run, before anything is pinned to it."""
    from hearth.integrations.durable import canonical
    from hearth.residents.models import Refused

    if not isinstance(tools, list) or not 1 <= len(tools) <= 32:
        raise Refused("management_tools_invalid")
    names = set()
    try:
        for tool in tools:
            if (
                not _TOOL_NAME.fullmatch(tool["name"])
                or tool["name"] in names
                or not isinstance(tool["inputSchema"], dict)
                or not isinstance(tool["description"], str)
            ):
                raise ValueError("invalid tool")
            names.add(tool["name"])
        if len(canonical(tools)) > 128 * 1024:
            raise ValueError("tool schemas too large")
    except KeyError, TypeError, ValueError, RecursionError:
        raise Refused("management_tools_invalid") from None
    return tools


def tool_names(tools: list[dict]) -> list[str]:
    """The `mcp__hearth__*` names of one run's tools, in the order they are offered."""
    return [TOOL_PREFIX + tool["name"] for tool in checked_tools(tools)]


def listing(tools: list[dict]) -> list[dict]:
    """Hearth's own tool specifications, as MCP describes a tool."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
        }
        for tool in tools
    ]


def configuration_pins(tools: list[dict]) -> dict:
    """What a management session is pinned to, digested before it is launched.

    `catalog_sha256` is Codex's name for "the model surface this run was configured
    with". On Claude that surface is the pinned CLI build and the pinned model, and on
    both it is a digest settlement compares without having to know what was digested.
    `tools_sha256` is the run's own tool list under the same function Codex digests it
    with, so one grant hashes to one value whichever runtime the run lands on.
    """
    from hearth.integrations.claude.config import MODEL, VERSION
    from hearth.integrations.durable import digest

    return {
        "catalog_sha256": digest({"version": VERSION, "model": MODEL}),
        "tools_sha256": digest(checked_tools(tools)),
    }


def configuration(python: str, path: Path) -> dict:
    """The `--mcp-config` document: one stdio server, carrying nothing of Hearth's."""
    return {
        "mcpServers": {
            SERVER_NAME: {
                "type": "stdio",
                "command": python,
                "args": ["-I", "-m", MODULE, str(path)],
                # Everything the shim needs, and nothing that could name a store, a
                # login or an owner token.
                "env": {"PATH": os.defpath},
            }
        }
    }


def session_tools(db, hearth, bound) -> tuple[list[dict], int]:
    """The tool list this run's live authority offers, and how many calls it allows.

    Read under the same authority the calls will be answered with, so a grant that
    changed between admission and launch produces a different digest, and the run is
    refused rather than launched with tools it no longer holds.
    """
    from hearth.management.bridge import authorize
    from hearth.management.tools import tool_specs
    from hearth.work.letters import run_letter_scope

    now = int(hearth.clock())
    authority = authorize(db, bound, now)
    letters = run_letter_scope(db, bound.run_id, now)
    tools = tool_specs(
        memory=authority["memory_writable"],
        management=authority["grant"]["enabled"],
        send_letters=letters["send"],
        reply_letter=letters["reply"],
        read_post=letters["post"],
    )
    return tools, int(authority["grant"]["max_calls"])


def refusal(code: str) -> dict:
    """A refusal the model can read, in the shape MCP gives a failed tool call."""
    return {"content": [{"type": "text", "text": json.dumps({"error": code})}], "isError": True}


def tool_result(response: dict) -> dict:
    """The native response `management.bridge` returns, as an MCP tool result."""
    return {
        "content": [{"type": "text", "text": item["text"]} for item in response["contentItems"]],
        "isError": not response["success"],
    }


class BridgeServer:
    """One run's socket, answered in the worker that owns the run.

    Held shut until the session's own `init` event has been checked against the pinned
    tool surface: nothing mutates in a session whose tools Hearth has not seen the
    provider agree to.
    """

    def __init__(self, folder: Path, *, hearth, bound, tools: list[dict], max_calls: int = 64):
        from hearth.management.bridge import Bridge

        self.folder = folder
        self.hearth = hearth
        self.bound = bound
        self.tools = checked_tools(tools)
        self.names = {tool["name"] for tool in self.tools}
        self.offered = tool_names(self.tools)
        self.listing = listing(self.tools)
        self.bridge = Bridge(hearth, bound)
        self.max_calls = max_calls
        # Refused calls are counted so that a session asking for tools it does not have
        # cannot write audit rows without end. The bound is the run's own call limit.
        self.refusals = 0
        self.trusted = False
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        # The code that ended the bridge, if anything did. A session whose bridge
        # failed is stopped and settles as failed; it is never left to be retried.
        self.failure: str | None = None
        self.listener: socket.socket | None = None
        self.selector: selectors.BaseSelector | None = None
        self.pending: dict[socket.socket, bytearray] = {}

    # -- lifecycle ---------------------------------------------------------

    @property
    def path(self) -> Path:
        """The socket the session's shim connects to.

        A sandboxed session reaches it because the launcher mounts this very path
        into the container as itself, so the configuration the shim was given names
        the same string inside and out and no translation layer can disagree with it.
        """
        return socket_path(self.folder)

    def open(self) -> None:
        self.listener = listen(socket_path(self.folder))

    def attach(self, selector: selectors.BaseSelector) -> None:
        assert self.listener is not None
        self.selector = selector
        selector.register(self.listener, selectors.EVENT_READ, None)

    def owns(self, fileobj) -> bool:
        return fileobj is self.listener or fileobj in self.pending

    def close(self) -> None:
        for connection in list(self.pending):
            self._drop(connection)
        if self.listener is not None:
            if self.selector is not None:
                with contextlib.suppress(KeyError, ValueError):
                    self.selector.unregister(self.listener)
            self.listener.close()
            self.listener = None
        with contextlib.suppress(OSError):
            socket_path(self.folder).unlink()

    def trust(self, thread_id: str, turn_id: str) -> None:
        """Bind the session the calls have to come from, then open the transport.

        Both identities are the CLI's own: the session it reported in `init`, and that
        event's identity as the run's single turn. They are pinned in `run_management`,
        so a call arriving under any other session is refused by `authorize` rather
        than by anything this process happens to remember.
        """
        self.bridge.bind_thread(thread_id)
        self.bridge.bind_turn(thread_id, turn_id)
        self.thread_id, self.turn_id = thread_id, turn_id
        self.trusted = True

    # -- transport ---------------------------------------------------------

    def ready(self, key) -> None:
        if key.fileobj is self.listener:
            self._accept()
        elif key.events & selectors.EVENT_WRITE:
            self._send(key.fileobj)
        else:
            self._receive(key.fileobj)

    def _accept(self) -> None:
        assert self.listener is not None and self.selector is not None
        try:
            connection, _ = self.listener.accept()
        except OSError:
            return
        if len(self.pending) >= MAX_CONNECTIONS:
            # The shim opens one connection per call and closes it either way, so a
            # queue this long is something that stopped speaking. The oldest goes,
            # which bounds the worker's descriptors without ever wedging the bridge.
            self._drop(next(iter(self.pending)))
        if peer_uid(connection) != os.getuid():
            # Only the user that owns the run reaches its tools, whatever the
            # permissions elsewhere on the host happen to allow. A sandboxed session
            # is that same uid -- the launcher starts it as Hearth's own -- so this
            # check holds across the boundary and is not weakened by it, as long as
            # nothing remaps uids between the two (see this module's own docstring).
            connection.close()
            return
        connection.setblocking(False)
        self.pending[connection] = bytearray()
        self.selector.register(connection, selectors.EVENT_READ, None)

    def _receive(self, connection) -> None:
        buffer = self.pending.get(connection)
        if buffer is None:
            return
        try:
            chunk = connection.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            self._drop(connection)
            return
        if not chunk:
            # The caller left before finishing a frame; nothing is owed to it.
            self._drop(connection)
            return
        buffer.extend(chunk)
        end = buffer.find(b"\n")
        if end < 0:
            if len(buffer) > MAX_FRAME:
                self._drop(connection)
            return
        frame = bytes(buffer[:end])
        try:
            answered = self.answer(frame)
        except BRIDGE_FAILURES:
            # Nothing a caller sends may leave the worker without a receipt, so a
            # failure here ends the session instead of ending this process.
            self.failure = "mcp_bridge_failed"
            answered = {"error": "mcp_bridge_failed"}
        self.pending[connection] = bytearray(
            json.dumps(answered, ensure_ascii=False).encode() + b"\n"
        )
        assert self.selector is not None
        self.selector.modify(connection, selectors.EVENT_WRITE, None)

    def _send(self, connection) -> None:
        outgoing = self.pending.get(connection)
        if outgoing is None:
            return
        try:
            sent = connection.send(outgoing)
        except BlockingIOError:
            return
        except OSError:
            self._drop(connection)
            return
        del outgoing[:sent]
        if not outgoing:
            self._drop(connection)

    def _drop(self, connection) -> None:
        self.pending.pop(connection, None)
        if self.selector is not None:
            with contextlib.suppress(KeyError, ValueError):
                self.selector.unregister(connection)
        with contextlib.suppress(OSError):
            connection.close()

    # -- answers -----------------------------------------------------------

    def answer(self, frame: bytes) -> dict:
        from hearth.integrations.durable import finite_float, reject_constant, unique_object

        try:
            request = json.loads(
                frame,
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
                parse_float=finite_float,
            )
        except ValueError, RecursionError:
            return {"error": "mcp_bridge_frame_invalid"}
        if not isinstance(request, dict):
            return {"error": "mcp_bridge_frame_invalid"}
        if request.get("op") == "tools/list":
            # Answered from the pinned list alone: no database, no authority, and no
            # dependence on the session being trusted yet, because the CLI asks for
            # this before it reports what it connected to.
            return {"result": {"tools": self.listing}}
        if request.get("op") != "tools/call":
            return {"error": "mcp_bridge_frame_invalid"}
        return {"result": self.call(request)}

    def call(self, request: dict) -> dict:
        from hearth.integrations.durable import short_string
        from hearth.residents.models import Refused

        call_id = request.get("call_id")
        tool = request.get("tool")
        arguments = request.get("arguments")
        if not short_string(call_id) or not short_string(tool) or not isinstance(arguments, dict):
            return refusal("management_call_invalid")
        assert isinstance(call_id, str) and isinstance(tool, str)
        if not self.trusted:
            return refusal("management_session_untrusted")
        if tool not in self.names:
            # A tool this run was never offered. The refusal is recorded where every
            # other management decision about this run is.
            if self.refusals < self.max_calls:
                self.refusals += 1
                self.record_refusal(call_id, tool)
            return refusal("management_tool_not_offered")
        # Retries must cross the owning writer again: it checks current authority
        # before replaying a durable receipt, rejecting a changed payload, or
        # enforcing the call limit. A transport cache cannot decide any of those.
        try:
            result = tool_result(
                self.bridge.call(
                    {
                        "threadId": self.thread_id,
                        "turnId": self.turn_id,
                        "callId": call_id,
                        "tool": tool,
                        "arguments": arguments,
                    }
                )
            )
        except Refused as error:
            return refusal(error.code)
        except BRIDGE_FAILURES:
            # Hearth's own writer failed. The session cannot be trusted to hold the
            # authority it was launched with, so it ends rather than continuing.
            self.failure = "mcp_bridge_failed"
            return refusal("mcp_bridge_failed")
        return result

    def record_refusal(self, call_id: str, tool: str) -> None:
        from hearth.management.bridge import authorize
        from hearth.residents.models import Refused
        from hearth.work.service import _audit

        try:
            with self.hearth.database.transaction(write=True) as db:
                now = int(self.hearth.clock())
                authority = authorize(db, self.bound, now)
                _audit(
                    db,
                    "management.tool_refused",
                    self.bound.run_id,
                    now,
                    {
                        "actor": authority["actor"],
                        "call_id": call_id,
                        "tool": tool,
                        "error": "management_tool_not_offered",
                    },
                )
        except Refused:
            # A run that has lost its authority refuses the call anyway. There is no
            # transaction left to record the refusal in, and nothing was changed.
            pass


def check_session(event: dict, offered: list[str]) -> tuple[str, str]:
    """The session's own account of what it connected to, against what Hearth pinned.

    The CLI reports the servers it reached and every tool it will offer the model in
    its `init` event, before the first model turn. Anything but exactly Hearth's own
    server and exactly the granted tools means the session in front of this bridge is
    not the session that was admitted, and it is stopped rather than trusted.

    Returns the two identities the run's calls are bound to: the CLI's session, and
    that event's own identity standing for the run's single turn.
    """
    from hearth.integrations.claude.config import MODEL, VERSION
    from hearth.integrations.durable import short_string
    from hearth.residents.models import Refused

    servers, tools = event.get("mcp_servers"), event.get("tools")
    if (
        not isinstance(servers, list)
        or len(servers) != 1
        or not isinstance(servers[0], dict)
        or servers[0].get("name") != SERVER_NAME
        or servers[0].get("status") != "connected"
        or not isinstance(tools, list)
        or sorted(name for name in tools if isinstance(name, str)) != sorted(offered)
        or len(tools) != len(offered)
    ):
        raise Refused("claude_tools_changed")
    session_id, turn_id = event.get("session_id"), event.get("uuid")
    if (
        event.get("model") != MODEL
        # The CLI reports its own build in the stream; the pin names the same one.
        or event.get("claude_code_version") != VERSION.split()[0]
        or not short_string(session_id)
        or not short_string(turn_id)
    ):
        raise Refused("claude_session_unpinned")
    assert isinstance(session_id, str) and isinstance(turn_id, str)
    return session_id, turn_id


def pin_configuration(hearth, bound) -> dict | None:
    """Record what this run's session will be launched with, before it is launched.

    None means the run reaches no Hearth tool at all and is launched with `--tools ""`.
    The pins are written under the same authority they were read from, so a grant that
    changes between here and the launch is caught by the worker's own check.
    """
    from hearth.residents.models import Refused

    with hearth.database.transaction(write=True) as db:
        row = db.execute("SELECT * FROM run_management WHERE run_id=?", (bound.run_id,)).fetchone()
        if row is None:
            return None
        tools, _ = session_tools(db, hearth, bound)
        pins = configuration_pins(tools)
        if any(row[key] is not None and row[key] != value for key, value in pins.items()):
            raise Refused("management_configuration_changed")
        db.execute(
            "UPDATE run_management SET catalog_sha256=?,tools_sha256=? WHERE run_id=?",
            (pins["catalog_sha256"], pins["tools_sha256"], bound.run_id),
        )
    return pins


def open_bridge(folder: Path, request: dict, hearth, python: str | None = None) -> BridgeServer:
    """Everything that has to hold before a management session is launched.

    The tool list is read from live authority and digested; a digest that is not the
    one admission pinned means the grant, the declaration or the letter this run was
    admitted for has changed, and the run is refused here -- before the CLI is
    launched, and before a cent is spent.

    `python` is the interpreter that will start the shim where the session runs: this
    worker's own by default, and the image's when the session is a sandboxed one, in
    which case Hearth's package is in that interpreter's own site directory. It is
    the launcher's answer (`Placement.interpreter`) and never anything a run said.
    """
    from hearth.integrations.durable import publish
    from hearth.management.bridge import BoundRun
    from hearth.residents.models import Refused

    expected = request["management"]
    bound = BoundRun(
        folder.name, request["owner"], request["epoch"], request["binding"]["input_digest"]
    )
    with hearth.database.transaction() as db:
        tools, max_calls = session_tools(db, hearth, bound)
        row = db.execute("SELECT * FROM run_management WHERE run_id=?", (bound.run_id,)).fetchone()
    pins = configuration_pins(tools)
    if row is None or pins != expected or any(row[key] != pins[key] for key in pins):
        raise Refused("management_configuration_changed")
    server = BridgeServer(folder, hearth=hearth, bound=bound, tools=tools, max_calls=max_calls)
    server.open()
    publish(
        configuration_path(folder),
        configuration(python or sys.executable, socket_path(folder)),
    )
    return server


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 2
    return serve_stdio(argv[1], sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
