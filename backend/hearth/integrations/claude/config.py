"""Pins, environment and the bounded session flags for the Claude Code CLI.

Every value here was measured against the pinned CLI on 2026-09-08 and written up in
`docs/claude-runtime.md`; nothing is assumed from documentation. Two measurements
shaped this file and are worth repeating where they are used:

- `--bare` cannot run under a subscription at all. It reads Anthropic credentials
  strictly from `ANTHROPIC_API_KEY` or an `apiKeyHelper`, never OAuth or the
  keychain, so a bounded subscription session is bounded by `--setting-sources ""`
  instead, which was measured to keep every settings file, hook, plugin and
  permission-mode override off the session.
- `--tools` decides which tools exist; it does not approve them. Under
  `--permission-prompts none` a tool that is listed but not pre-approved is denied
  automatically. So `session_command` writes both flags, from the same list of
  `mcp__hearth__*` names the run's grant allows.
"""

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

KIND = "claude_subscription"
# The pinned CLI, verified with `claude --version` before anything else runs.
VERSION = "2.1.263 (Claude Code)"
# One model, standard mode, low effort to match the Codex adapter's reasoning effort.
MODEL = "claude-opus-5"
EFFORT = "low"
# `system_meta` key holding the sha256 of the binary this store was configured with.
BINARY_PIN = "claude_live_binary"
# The CLI is a ~200 MB single file and starts a Node runtime; the Codex adapter's five
# seconds is too tight for a cold start of it.
PROBE_TIMEOUT = 30
# How long one headless turn may run before the worker signals its process group. The
# Codex adapter allows two minutes; a cold Node start plus an Opus turn needs more, and
# the provider's own `--max-budget-usd` fence stops a session that is merely expensive.
RUN_TIMEOUT = 600

# The bounded session every run is launched with. The run's own arguments -- model,
# effort, MCP configuration, tool list and budget fence -- are added by
# `session_command` below, and the prompt is delivered on stdin.
SESSION_FLAGS: tuple[str, ...] = (
    "--print",
    # The stream is the receipt: every API response's usage block is kept.
    "--output-format",
    "stream-json",
    "--verbose",
    # No settings file may register a hook, load a plugin or change the permission mode.
    "--setting-sources",
    "",
    # No MCP server from the machine; only what Hearth passes with --mcp-config.
    "--strict-mcp-config",
    "--no-session-persistence",
    "--disable-slash-commands",
    # Nobody can answer a prompt in a headless run, so anything that would ask is denied.
    "--permission-prompts",
    "none",
)


def session_command(
    binary: Path,
    *,
    budget_usd: str,
    tools: Sequence[str] = (),
    mcp_config: Path | None = None,
) -> list[str]:
    """The whole argv of one bounded headless run. The prompt is delivered on stdin.

    `tools` are the `mcp__hearth__*` names of the run's own grant, carried into the
    session by the bridge (`mcp_bridge.py`) that `mcp_config` names. A run that
    reaches no Hearth tool is launched with `--tools ""`, which was measured to report
    `"tools": []` in the session's own `init` event.

    Both tool flags are written, because they were measured to do different things:
    `--tools` decides which tools exist, and under `--permission-prompts none` a tool
    that is not also named to `--allowedTools` is denied on every call.

    `--max-budget-usd` is a second fence, not a ceiling: it was measured to stop the
    session only *after* a request has already been billed past it, so Hearth's own
    admission hold stays the authority and the receipt records that the CLI stopped
    on the fence.
    """
    command = [str(binary), *SESSION_FLAGS, "--model", MODEL, "--effort", EFFORT]
    if mcp_config is not None:
        command += ["--mcp-config", str(mcp_config)]
    names = ",".join(tools)
    command += ["--tools", names] + (["--allowedTools", names] if names else [])
    return command + ["--max-budget-usd", budget_usd]


def budget(microdollars: int) -> str:
    """The microdollars a caller fenced the session at, as the dollars the CLI takes.

    Not the run's reservation: that is an admission hold of a cent on every path here,
    and a session stopped at a cent is a session billed and then thrown away. What the
    fence is, and why it may exceed the hold, is `work.service.spend_fence`.

    Microdollars are exact to six decimal places, so this conversion neither invents
    money nor rounds any of it away.
    """
    if type(microdollars) is not int or microdollars < 0:
        raise ValueError("invalid budget")
    return f"{microdollars / 1_000_000:.6f}"


def binary_digest(path: Path) -> str:
    """The pinned binary's sha256, read in chunks: the CLI is around 200 MB."""
    with path.open("rb") as binary:
        return hashlib.file_digest(binary, "sha256").hexdigest()


def environment(config_dir: Path) -> dict[str, str]:
    """The whole environment a Claude session gets: a search path and its own login.

    `DISABLE_AUTOUPDATER` keeps the pinned binary from being replaced under its own
    pin. The installer keeps versioned files and moves a symlink, so Hearth pins the
    versioned file itself and an update cannot change the bytes behind the hash.

    `USER` is the one machine fact that travels: on macOS the CLI keeps the login in
    the Keychain under the account name, and without `USER` in its environment it
    answers `loggedIn: false` for a directory that is logged in (measured on 2.1.263,
    2026-09-09: `env -i PATH=… CLAUDE_CONFIG_DIR=$CFG` is not logged in, adding
    `USER=$USER` alone is). It names nobody's secret; the login itself stays in the
    Keychain.
    """
    env = {
        "PATH": os.defpath,
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "DISABLE_AUTOUPDATER": "1",
    }
    user = account_name()
    if user:
        env["USER"] = user
    return env


def account_name() -> str | None:
    """The account the process runs as, from the system, not from its environment.

    The detached worker is launched with a search path and nothing else, so `USER`
    cannot be inherited; the uid can always be asked.
    """
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except ImportError, KeyError, OSError:
        return os.environ.get("USER")


def logged_in(status: str) -> bool:
    """Read `claude auth status --json` for the one fact Hearth is allowed to keep.

    The answer also carries the account's email, organisation and plan. None of it is
    read, logged or stored: a login either exists in that config dir or it does not.
    """
    try:
        answer = json.loads(status)
    except ValueError:
        return False
    return isinstance(answer, dict) and answer.get("loggedIn") is True
