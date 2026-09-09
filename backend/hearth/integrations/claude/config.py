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
  automatically. The tool list and its `--allowedTools` twin land with the bridge
  (#147), which is why they are not in `SESSION_FLAGS` yet.
"""

import json
import os
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

# The bounded session every run is launched with. The run's own arguments (prompt,
# model, budget ceiling, tools, MCP config) are added by the worker in #146 and #147.
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


def environment(config_dir: Path) -> dict[str, str]:
    """The whole environment a Claude session gets: a search path and its own login.

    `DISABLE_AUTOUPDATER` keeps the pinned binary from being replaced under its own
    pin. The installer keeps versioned files and moves a symlink, so Hearth pins the
    versioned file itself and an update cannot change the bytes behind the hash.
    """
    return {
        "PATH": os.defpath,
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "DISABLE_AUTOUPDATER": "1",
    }


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
