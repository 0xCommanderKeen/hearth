"""Bounded private stdio process: native RPC is the only management call channel."""

import hashlib
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from hearth.integrations.codex import app_server_config as config
from hearth.integrations.codex.events import (
    MAX_EVENTS,
    MAX_RECORD,
    finite_float,
    reject_constant,
    short_string,
    unique_object,
)
from hearth.integrations.codex.pricing import MODEL
from hearth.integrations.codex.subscription import AUTH
from hearth.integrations.launcher import LOGIN, Placement, ProcessLauncher
from hearth.residents.models import Refused

PROTOCOL = "codex-app-server-0.153.4"
_MAX_CONFIGURE_FRAME = 2 * 1024 * 1024
_MAX_CONFIGURE_PARAMS = 1_500_000
MAX_NATIVE_STREAM = 8 * 1024 * 1024
_NO_INPUT = (
    "Interactive input is disabled. Continue within the supplied "
    "Hearth policy or report its refusal."
)
_RECORDED = {
    "thread/started",
    "turn/started",
    "thread/tokenUsage/updated",
    "item/tool/call",
    "item/completed",
    "turn/completed",
    "error",
}


def configuration_pins(binary: Path, tools: list[dict]) -> dict:
    config.tool_names(tools)
    return {
        "catalog_sha256": hashlib.sha256(config.model_catalog(binary.resolve())).hexdigest(),
        "tools_sha256": config.digest(tools),
    }


def _configuration_params(params):
    return (
        isinstance(params, dict)
        and set(params) <= {"threadId", "turnId", "callId", "tool", "namespace", "arguments"}
        and all(short_string(params.get(key)) for key in ("threadId", "turnId", "callId"))
        and params.get("namespace") in (None, "functions")
        and params.get("tool") == "hearth_residents_configure"
        and isinstance(params.get("arguments"), dict)
        and len(json.dumps(params, ensure_ascii=False).encode()) <= _MAX_CONFIGURE_PARAMS
    )


def _configuration_item(item, thread_id, turn_id):
    if not isinstance(item, dict) or item.get("type") != "dynamicToolCall":
        return False
    return _configuration_params(
        {
            "threadId": thread_id,
            "turnId": turn_id,
            "callId": item.get("id"),
            "tool": item.get("tool"),
            "namespace": item.get("namespace"),
            "arguments": item.get("arguments"),
        }
    )


def _large_configuration_record(message):
    """The pinned CLI repeats configure arguments around the native callback.

    Only recognized configure-bearing records may exceed the ordinary record
    limit. Stripping their validated argument payloads must leave an ordinary
    bounded record, so unrelated output cannot borrow this allowance.
    """
    params = message.get("params")
    if not isinstance(params, dict):
        return False
    method = message.get("method")
    if method == "item/tool/call":
        return (
            set(message) <= {"id", "method", "params", "jsonrpc"}
            and message.get("jsonrpc", "2.0") == "2.0"
            and type(message.get("id")) in {int, str}
            and _configuration_params(params)
        )
    if "id" in message or not short_string(params.get("threadId")):
        return False
    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        if not _configuration_item(item, params["threadId"], params.get("turnId")):
            return False
        remainder = message | {"params": params | {"item": item | {"arguments": {}}}}
    elif method == "turn/completed":
        turn = params.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
            return False
        found = False
        items = []
        for item in turn["items"]:
            if _configuration_item(item, params["threadId"], turn.get("id")):
                found = True
                items.append(item | {"arguments": {}})
            else:
                items.append(item)
        if not found:
            return False
        remainder = message | {"params": params | {"turn": turn | {"items": items}}}
    else:
        return False
    return len(config.canonical(remainder)) <= MAX_RECORD


class _Pipe:
    def __init__(self, child, deadline, cancelled):
        self.child = child
        assert child.stdin is not None and child.stdout is not None
        self.stdin = child.stdin.fileno()
        self.stdout = child.stdout.fileno()
        os.set_blocking(self.stdin, False)
        os.set_blocking(self.stdout, False)
        self.deadline = deadline
        self.cancelled = cancelled
        self.buffer = bytearray()
        self.total = self.records = self.sequence = 0

    def check(self):
        if self.cancelled():
            raise Refused("app_server_cancelled")
        if time.monotonic() >= self.deadline:
            raise Refused("app_server_timeout")

    def send(self, message):
        data = config.canonical(message) + b"\n"
        if len(data) > MAX_RECORD:
            raise Refused("app_server_message_too_large")
        offset = 0
        with selectors.DefaultSelector() as selector:
            selector.register(self.stdin, selectors.EVENT_WRITE)
            while offset < len(data):
                self.check()
                if not selector.select(0.05):
                    continue
                try:
                    offset += os.write(self.stdin, data[offset:])
                except BlockingIOError:
                    continue

    def receive(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.stdout, selectors.EVENT_READ)
            while True:
                self.check()
                end = self.buffer.find(b"\n")
                if end >= 0:
                    if end > _MAX_CONFIGURE_FRAME:
                        raise Refused("app_server_message_too_large")
                    line = bytes(self.buffer[:end])
                    del self.buffer[: end + 1]
                    self.records += 1
                    if self.records > MAX_EVENTS:
                        raise Refused("app_server_transcript_too_large")
                    if not line.strip():
                        if end > MAX_RECORD:
                            raise Refused("app_server_message_too_large")
                        continue
                    try:
                        message = json.loads(
                            line,
                            object_pairs_hook=unique_object,
                            parse_constant=reject_constant,
                            parse_float=finite_float,
                        )
                    except ValueError, RecursionError:
                        raise Refused("app_server_protocol_invalid") from None
                    if not isinstance(message, dict):
                        raise Refused("app_server_protocol_invalid")
                    if end > MAX_RECORD and not _large_configuration_record(message):
                        raise Refused("app_server_message_too_large")
                    return message
                if len(self.buffer) > _MAX_CONFIGURE_FRAME:
                    raise Refused("app_server_message_too_large")
                if not selector.select(0.05):
                    continue
                try:
                    chunk = os.read(self.stdout, 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise Refused("app_server_disconnected")
                self.total += len(chunk)
                if self.total > MAX_NATIVE_STREAM:
                    raise Refused("app_server_transcript_too_large")
                self.buffer.extend(chunk)

    def start_request(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        return request_id

    def response(self, request_id, observe):
        while True:
            message = self.receive()
            if "method" not in message:
                if message.get("id") != request_id or "result" not in message:
                    raise Refused("app_server_request_failed")
                return message["result"]
            if "id" in message:
                raise Refused("app_server_unexpected_request")
            observe(message)

    def request(self, method, params, *, observe=lambda message: None):
        return self.response(self.start_request(method, params), observe)


@contextmanager
def _process(
    program, home, workspace, settings, deadline, cancelled, launcher, mounts=(), started=None
):
    """One app-server session, wherever this run was admitted to execute.

    `program` and `home` are already the paths the session itself names -- the image's
    own CLI and the login mounted at the path `CODEX_HOME` will hold -- so the argv
    below is the same on both launchers and only the strings differ. `workspace` stays
    a host path: it is the client's own working directory, and what the session sees
    of it is the empty tmpfs the launcher mounts.
    """
    started = (lambda handle: None) if started is None else started
    handle = launcher.start(
        [program, "app-server", "--strict-config", "--stdio", *config.arguments(settings)],
        env={"PATH": os.defpath, "CODEX_HOME": home},
        cwd=workspace,
        stdin=subprocess.PIPE,
        bufsize=0,
        mounts=mounts,
    )
    # What was started, told to the caller before the id is asked for and again after:
    # asking waits, and a worker that dies while waiting would otherwise leave a live
    # container that nothing on disk names. A management run starts two of these, one
    # after the other, and each in turn is the one that has to be findable.
    started(handle)
    # Asked for outside Hearth's dispatch guard, which this process is deliberately
    # created before entering.
    launcher.identify(handle)
    started(handle)
    child = handle.process
    try:
        yield _Pipe(child, deadline, cancelled)
    finally:
        if child.poll() is None:
            launcher.stop(handle, signal.SIGTERM)
            if launcher.wait(handle, 2) is None:
                launcher.stop(handle, signal.SIGKILL)
                launcher.wait(handle, 2)
        if child.stdin is not None:
            child.stdin.close()
        if child.stdout is not None:
            child.stdout.close()


def _initialize(pipe, workspace, settings):
    pipe.request(
        "initialize",
        {
            "clientInfo": {"name": "hearth", "version": "1"},
            "capabilities": {"experimentalApi": True},
        },
    )
    pipe.send({"method": "initialized", "params": {}})
    config.check_config(
        pipe.request("config/read", {"cwd": str(workspace), "includeLayers": True}),
        settings,
    )
    return pipe.request("skills/list", {"cwds": [str(workspace)], "forceReload": True})


def _tool_response(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"contentItems", "success"}
        or type(value["success"]) is not bool
        or not isinstance(value["contentItems"], list)
        or not 1 <= len(value["contentItems"]) <= 16
        or any(
            not isinstance(item, dict)
            or set(item) != {"type", "text"}
            or item["type"] != "inputText"
            or not isinstance(item["text"], str)
            for item in value["contentItems"]
        )
        or len(config.canonical(value)) > 256 * 1024
    ):
        raise Refused("app_server_tool_response_invalid")
    return value


def _tool_call(message, thread_id, turn_id, names, on_tool, seen, max_calls):
    params = message["params"]
    if (
        not isinstance(params, dict)
        or params.get("threadId") != thread_id
        or params.get("turnId") != turn_id
        or params.get("namespace") not in {None, "functions"}
        or params.get("tool") not in names
        or not short_string(params.get("callId"))
        or not isinstance(params.get("arguments"), dict)
        or type(message.get("id")) not in {int, str}
    ):
        raise Refused("app_server_tool_request_invalid")
    call_id = params["callId"]
    fingerprint = config.digest(params)
    if call_id in seen:
        previous, response = seen[call_id]
        if previous != fingerprint:
            raise Refused("app_server_tool_request_conflict")
        return response
    if len(seen) >= max_calls:
        raise Refused("app_server_tool_limit")
    try:
        response = on_tool(params)
    except Refused as refusal:
        response = {
            "contentItems": [{"type": "inputText", "text": json.dumps({"error": refusal.code})}],
            "success": False,
        }
    response = _tool_response(response)
    seen[call_id] = (fingerprint, response)
    return response


def run(
    *,
    binary: Path,
    auth_home: Path,
    workspace: Path,
    prompt: str,
    tools: list[dict],
    on_thread,
    on_turn,
    on_tool,
    cancelled,
    dispatch_guard,
    timeout: float = 600,
    max_calls: int = 64,
    expected_pins: dict | None = None,
    launcher=None,
    # Told what each of this run's sessions started, so that the caller -- which is the
    # only thing here with a run folder to write in -- can record it. A container whose
    # worker dies is only findable by what was written down.
    on_session=None,
) -> dict:
    """One private native process/turn; callbacks retain Hearth's transactional authority.

    No credential, application database or owner token is passed through this interface
    to Codex. The tool callback receives only the actual structured native request.
    """
    result: dict = {
        "protocol": PROTOCOL,
        "launched": False,
        "cancelled": False,
        "error": None,
        "exit_code": None,
        "events": [],
    }
    process = None
    # Where this session's processes are started. The default is the launcher Hearth
    # has always used, so a caller that names none is launched exactly as before.
    launcher = ProcessLauncher() if launcher is None else launcher
    # And what its own paths are. On the process launcher every one of them is the
    # host path it always was; in a container the CLI is the image's, the login and
    # the generated settings are mounts, and the working directory is a tmpfs.
    placement = Placement(launcher.kind)
    try:
        if not 0 < timeout <= 600 or type(max_calls) is not int or not 1 <= max_calls <= 64:
            raise Refused("app_server_limits_invalid")
        if not isinstance(prompt, str) or len(prompt.encode()) > 512 * 1024:
            raise Refused("app_server_prompt_invalid")
        binary, auth_home, workspace = binary.resolve(), auth_home.resolve(), workspace.resolve()
        if not workspace.is_dir() or any(workspace.iterdir()):
            raise Refused("app_server_workspace_unsafe")
        names = config.tool_names(tools)
        config.check_auth_home(auth_home)
        deadline = time.monotonic() + timeout
        with tempfile.TemporaryDirectory(prefix="hearth-codex-management-") as temporary:
            catalog = config.model_catalog(binary)
            pins = {
                "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
                "tools_sha256": config.digest(tools),
            }
            if expected_pins is not None and pins != expected_pins:
                raise Refused("app_server_configuration_changed")
            result.update(pins)
            catalog_path = Path(temporary) / "models.json"
            catalog_path.write_bytes(catalog)
            catalog_path.chmod(0o600)
            # The three paths this session names, and the mounts that put them there.
            # The catalog is generated here from the CLI this store is pinned to and
            # read inside as a file the session may not change.
            program = placement.binary(binary, "codex")
            home = placement.login(auth_home, AUTH, LOGIN)
            catalog_json = placement.same(temporary) + "/models.json"
            inside = placement.workspace(workspace)
            mounts = placement.mounts
            settings = config.settings() | {"model_catalog_json": catalog_json}
            # Discovery never starts a thread/turn; a second isolated process starts
            # with every discovered skill disabled, then verifies the effective set.
            with _process(
                program,
                home,
                workspace,
                settings,
                deadline,
                cancelled,
                launcher,
                mounts,
                on_session,
            ) as discovery:
                paths = config.skill_paths(_initialize(discovery, inside, settings))
            settings["skills.config"] = [{"path": path, "enabled": False} for path in paths]
            with _process(
                program,
                home,
                workspace,
                settings,
                deadline,
                cancelled,
                launcher,
                mounts,
                on_session,
            ) as process:
                actual_paths = config.skill_paths(
                    _initialize(process, inside, settings), disabled=True
                )
                if actual_paths != paths:
                    raise Refused("app_server_skills_changed")

                def observe(message):
                    # A reroute violates the pinned model; compaction may hide
                    # provider work from the bounded turn's accounting evidence.
                    if message.get("method") in {"model/rerouted", "thread/compacted"}:
                        raise Refused("app_server_execution_changed")
                    if message.get("method") in _RECORDED:
                        result["events"].append(message)

                thread = process.request(
                    "thread/start",
                    {
                        "model": MODEL,
                        "modelProvider": "openai",
                        # The session's own working directory, as the session names it.
                        "cwd": inside,
                        "ephemeral": True,
                        "approvalPolicy": "never",
                        "permissions": "reader",
                        "developerInstructions": config.INSTRUCTIONS,
                        "dynamicTools": tools,
                        "allowProviderModelFallback": False,
                    },
                    observe=observe,
                )
                thread_id = config.check_thread(thread)
                if not short_string(thread_id):
                    raise Refused("app_server_thread_unsafe")
                on_thread(thread_id)
                config.check_auth_home(auth_home)
                if hashlib.sha256(catalog_path.read_bytes()).hexdigest() != pins["catalog_sha256"]:
                    raise Refused("app_server_configuration_changed")
                with dispatch_guard():
                    process.check()
                    result["launched"] = True
                    request_id = process.start_request(
                        "turn/start",
                        {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]},
                    )
                turn = process.response(request_id, observe)
                turn_id = turn["turn"]["id"]
                if not short_string(turn_id):
                    raise Refused("app_server_turn_invalid")
                on_turn(thread_id, turn_id)
                seen = {}
                while True:
                    message = process.receive()
                    if "method" not in message:
                        raise Refused("app_server_protocol_invalid")
                    observe(message)
                    method = message["method"]
                    if "id" in message:
                        if method == "item/tool/call":
                            response = _tool_call(
                                message, thread_id, turn_id, names, on_tool, seen, max_calls
                            )
                            process.send({"id": message["id"], "result": response})
                        elif method == "item/tool/requestUserInput":
                            params = message["params"]
                            if (
                                params.get("threadId") != thread_id
                                or params.get("turnId") != turn_id
                            ):
                                raise Refused("app_server_tool_request_invalid")
                            answers = {
                                question["id"]: {"answers": [_NO_INPUT]}
                                for question in params["questions"]
                            }
                            process.send({"id": message["id"], "result": {"answers": answers}})
                        else:
                            raise Refused("app_server_unexpected_request")
                    if method == "turn/completed":
                        break
    except Refused as error:
        result["error"] = error.code
        result["cancelled"] = error.code == "app_server_cancelled"
    except OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, RecursionError:
        result["error"] = "app_server_transport_failed"
    finally:
        if process is not None:
            result["exit_code"] = process.child.returncode
    return result
