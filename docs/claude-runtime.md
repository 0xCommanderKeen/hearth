# The Claude Code CLI as a Hearth runtime

Everything Hearth's `claude_subscription` runtime relies on about the CLI was measured
against the pinned build, not read from documentation. This is the record: what was run,
what came back, and what did not measure the way the plan assumed. A later slice that
needs a fact not in here measures it and adds it.

Measured on **2026-09-08**, macOS (Darwin 25.5.0, arm64), by the slice that introduced
the runtime (#145).

| Pin | Value |
| --- | --- |
| CLI | `2.1.263 (Claude Code)` |
| Binary | `~/.local/share/claude/versions/2.1.263` |
| sha256 | `ef5d2909c8af49f31ab6d5487e90316777bc2fac170adfe8160716caa8aaf4f9` |
| Model | `claude-opus-5`, standard mode, `--effort low` |

Two shorthands below: `$CFG` is an empty private configuration directory and `$WORK` an
empty working directory, both under a temporary path. Unless a row says otherwise, a
command ran as

```sh
cd $WORK && env -i PATH=/usr/bin:/bin CLAUDE_CONFIG_DIR=$CFG \
  ~/.local/share/claude/versions/2.1.263 <arguments>
```

No credential, token, keychain item or configuration-directory content was read, copied
or recorded while measuring. `claude auth status --json` also answers with the account's
email, organisation and plan; only `loggedIn` was ever looked at, and the runtime reads
nothing else from it either.

## Spike 1 — does a private `CLAUDE_CONFIG_DIR` have its own login?

| Command | Observed |
| --- | --- |
| `auth status --json` with `CLAUDE_CONFIG_DIR=$CFG` | `{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty", "projectsDirectory": "$CFG/projects"}`, exit 0 |
| `auth status --json` with the machine's own configuration directory | `loggedIn: true`, `authMethod: "claude.ai"`; the rest of the answer names the account and was not recorded |
| `-p` with the full bounded flag set under `CLAUDE_CONFIG_DIR=$CFG` | exit 1, `is_error: true`, `terminal_reason: "api_error"`, `result: "Not logged in · Please run /login"`, `total_cost_usd: 0`, `modelUsage: {}` |

**A private configuration directory is a private login.** The Keychain login of the
default directory is not visible from `$CFG`: the CLI reports no login and refuses to
run. So the Codex adapter's shape carries over — a private login directory the operator
seeds once — and Hearth's own check is `auth status --json` reporting `loggedIn: true`
in exactly the directory the run will use.

`auth status` **exits 0 whether or not there is a login**, so the exit code proves
nothing and the JSON has to be read.

**Not measured: the operator step that seeds the private login.** `claude auth login`
inside `$CFG` needs a browser and the account holder, so it was not run here. Whether it
then stores the token as a separate Keychain item or as a file under `$CFG` is therefore
unknown on macOS, and so is whether a private login can be seeded by copying anything.
Until it is measured, the operator seeds it by hand:

```sh
CLAUDE_CONFIG_DIR=/path/to/private-claude-config claude auth login
```

The runtime refuses `claude_subscription_login_required` until that has happened.

## Spike 2 — the NAS (Linux)

**Not measured.** Nothing in this slice ran on the NAS. Before the deployed instance can
run on Claude, the same three commands have to be run there, and the question the plan
asks — whether credentials live as a file under the configuration directory and can be
copied the way Codex's `auth.json` is — has to be answered from that host, not from
this Mac.

## Spike 3 — can the pinned binary change under its own pin?

| Command | Observed |
| --- | --- |
| `shasum -a 256 ~/.local/share/claude/versions/2.1.263` before and after six sessions | `ef5d2909…f4f9` both times |
| `ls -l ~/.local/bin/claude` | a symlink, pointing at `versions/2.1.265` |
| `ls ~/.local/share/claude/versions/` | `2.1.260 2.1.261 2.1.263 2.1.265` |

**The installer never rewrites a version file; it installs a new one and moves the
symlink.** The symlink had already moved from 2.1.263 to 2.1.265 earlier the same day,
while 2.1.263's bytes stayed identical. Hearth therefore pins the versioned file itself
(`HEARTH_CLAUDE_BINARY=~/.local/share/claude/versions/2.1.263`), which an update cannot
change, and refuses `claude_subscription_binary_changed` if the bytes behind that path
ever do.

`DISABLE_AUTOUPDATER=1` is set on every session Hearth launches, and no update ran
during any of them — but no update was due either, so **the flag's effect was not
isolated**. It is belt to the pin's braces, not the thing being trusted.

## The bounded session, flag by flag

Every row ran from `$WORK` with `-p --output-format stream-json --verbose --model
claude-opus-5 --effort low` plus the flags named. The rows that need a login ran with
the machine's own configuration directory, because a private login could not be seeded
here (spike 1); nothing else about the sessions differed. `$WORK/.claude/settings.json`
held a `SessionStart` hook that touches a file, planted to see whether settings files
reach the session.

| Flag | Expected | Observed | Verdict |
| --- | --- | --- | --- |
| `--bare` | minimal session | `result: "Not logged in · Please run /login"`, no API call. `--help`: "Anthropic auth is strictly `ANTHROPIC_API_KEY` or `apiKeyHelper` … OAuth and keychain are never read" | **Refuted.** `--bare` cannot run under a subscription at all |
| `--setting-sources ""` | no hook from a planted settings file fires | the hook file was **not** created; `init` reported `plugins: []`, `permissionMode: "default"` | Holds |
| _control_: the same run without it | — | the hook **fired**, four machine plugins loaded, `permissionMode: "auto"` (from the user's own settings) | The flag is load-bearing |
| `--tools "" --strict-mcp-config` | zero tools, no host MCP server | `init`: `"tools": []`, `"mcp_servers": []`, `"slash_commands": []`, `"skills": []` | Holds |
| `--tools mcp__hearthprobe__ping --strict-mcp-config --mcp-config <file>` | exactly that tool | `init`: `"tools": ["mcp__hearthprobe__ping"]`, `"mcp_servers": [{"name": "hearthprobe", "status": "connected"}]` | Holds |
| `--permission-prompts none` | anything that would prompt is denied | the model called the one tool it had; the call was **denied automatically**, `permission_denials` named it, and the turn still ended `is_error: false` | Holds — with a consequence, below |
| the same run plus `--allowedTools mcp__hearthprobe__ping` | — | the call ran, `result: "pong"`, `permission_denials: []` | `--tools` grants existence, `--allowedTools` grants permission |
| `--no-session-persistence` | nothing written under the configuration directory's `projects` | no session transcript was written after six sessions — but the CLI still **creates** `<config>/projects/<cwd-slug>/memory/` and writes `.claude.json` and `backups/` at the configuration directory's root | Holds for session content only |
| `--disable-slash-commands` | no skills or commands | `init`: `"slash_commands": []`, `"skills": []`. Built-in **agents** are still listed (`claude`, `Explore`, `general-purpose`, `Plan`) | Holds for commands; agents are a separate surface |
| `--effort low` | accepted | accepted; the session's effort is not echoed anywhere in the stream | Not independently observable |
| `--max-budget-usd 0.01` | the CLI stops on the fence | the turn **completed and was billed** (`total_cost_usd: 0.036474`), then the result said `subtype: "error_max_budget_usd"`, `errors: ["Reached maximum budget ($0.01)"]`, `terminal_reason: "budget_exhausted"`, `is_error: true`, exit 1 | Holds as a *stop*, not as a *ceiling* |
| `--fallback-model` absent | no reroute field in the output | no reroute, fallback or model-change field appears anywhere in the result event | Holds (absence only; a reroute was not provoked) |

### What the stream says about usage

Two observations from the same runs decide how #146 can price anything.

- **The result event's own `usage` block is zeroed.** Every field of `usage` came back
  `0` while the turn had really spent tokens. The truthful numbers are in each
  `assistant` message's `message.usage` (`input_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `output_tokens`) and, per model, in the result's
  `modelUsage`. Pricing reads the per-request usage; `total_cost_usd` and `modelUsage`
  stay the cross-check.
- **A session spends more than one model.** A one-word answer's `modelUsage` named both
  `claude-opus-5` (the turn) and `claude-haiku-4-5-20251001` (899 input, 9 output tokens,
  the CLI's own housekeeping). `total_cost_usd` is the sum of both. A rule of "a receipt
  naming another model leaves usage unknown" would therefore hold every single run: the
  pinned-model check has to be about the *turn's* model, and the secondary model's
  tokens have to be priced or explicitly excluded, deliberately.

### Consequences for the design

1. **`--bare` is out of the flag set.** A bounded subscription session is bounded by
   `--setting-sources ""`, which was measured to keep every settings file, hook, plugin
   and permission-mode override off the session. `--safe-mode` (which keeps auth normal)
   is the untested alternative if more is ever needed.
2. **The tool list and the permission list are two different flags.** `--tools` decides
   which tools exist; under `--permission-prompts none` a tool that is not also named to
   `--allowedTools` is denied on every call. The bridge (#147) passes both.
3. **`--max-budget-usd` is a second fence, not a ceiling.** It stops the session after a
   request has already been billed past it, so Hearth's admission hold stays the
   authority and the receipt records that the CLI stopped on the fence.
4. **The configuration directory is written to even in a bounded session.** It must be a
   directory Hearth owns and the operator seeded, never the machine's own `~/.claude`.

## Configuring Hearth for it

```sh
HEARTH_CLAUDE_BINARY=~/.local/share/claude/versions/2.1.263 \
HEARTH_CLAUDE_CONFIG_DIR=/path/to/private-claude-config \
HEARTH_OPERATOR_TOKEN=... \
uv run uvicorn hearth.app:from_env --factory --host 127.0.0.1 --port 8766
```

Hearth builds the adapter its own store records. On a store whose `runtime_kind` is
`claude_subscription` it checks the version, checks the login, pins the binary's sha256
into `system_meta.claude_live_binary` with a `runtime.claude_subscription_configured`
audit fact, and refuses by name — `claude_subscription_configuration_required`,
`claude_subscription_version_unsupported`, `claude_subscription_login_required`,
`claude_subscription_binary_changed` — writing nothing when it does. No run executes on
this runtime yet; that is #146.
