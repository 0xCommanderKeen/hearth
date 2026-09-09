# The Claude Code CLI as a Hearth runtime

Everything Hearth's `claude_subscription` runtime relies on about the CLI was measured
against the pinned build, not read from documentation. This is the record: what was run,
what came back, and what did not measure the way the plan assumed. A later slice that
needs a fact not in here measures it and adds it.

Measured on **2026-09-08**, macOS (Darwin 25.5.0, arm64), by the slice that introduced
the runtime (#145), and extended on **2026-09-09** by the headless run (#146) and the
tool bridge (#147), both of which recorded real sessions and read their numbers. Where
two measurements disagree, the later one says so in place and wins.

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

**A private configuration directory is a private login.** The machine's own login is a
Keychain item — the default configuration directory holds no `.credentials.json` — and
it is not visible from `$CFG`: the CLI reports no login there and refuses to run. So the
Codex adapter's shape carries over — a private login directory the operator
seeds once — and Hearth's own check is `auth status --json` reporting `loggedIn: true`
in exactly the directory the run will use.

**Sharper than that, measured 2026-09-09: it is setting the variable at all that
changes the login, not the path it names.**

| Command | Observed |
| --- | --- |
| `auth status --json`, no `CLAUDE_CONFIG_DIR` in the environment | `loggedIn: true`, `authMethod: "claude.ai"` |
| `auth status --json` with `CLAUDE_CONFIG_DIR=$HOME/.claude` — the same directory the line above used | `loggedIn: false`, `authMethod: "none"` |
| `auth status --json` with `PATH=/usr/bin:/bin` and no `CLAUDE_CONFIG_DIR` | `loggedIn: true` |

An explicitly configured directory gets its own login namespace even when it *is* the
default one, so the operator's seeding step cannot be skipped by pointing Hearth at
`~/.claude`, and a stripped environment is not what hides the machine's login. Hearth
always sets `CLAUDE_CONFIG_DIR`, so every session it launches is on the private login
or on none.

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

This is the section #146 is built on, and three of its rows came back against the plan.
The transcripts behind it are committed, scrubbed, under
`tests/integrations/claude/fixtures/`.

| Place the CLI reports usage | On a clean success | On a `--max-budget-usd` stop |
| --- | --- | --- |
| `result.usage` (top level) | truthful: the turn's totals | **every field `0`** |
| `result.usage.iterations` | one row per request, truthful | **empty** |
| `result.modelUsage` | truthful, per model, with each model's own `costUSD` | truthful |
| `result.total_cost_usd` | truthful | truthful |
| an `assistant` message's `message.usage` | **`output_tokens: 1` where the request billed 4** | same |

- **`result.usage` cannot be trusted as usage.** #145 saw it zeroed; #146 saw it
  truthful on a success and zeroed again on the budget stop. Neither reading is safe to
  build on, so Hearth never reads it as usage — only `usage.iterations` and `modelUsage`.
- **An `assistant` message's `usage` is a mid-stream snapshot.** Its `output_tokens`
  undercounts the request that produced it. Its *cache* numbers were final in every
  recording, and it is the only place the five-minute / one-hour cache-write split
  (`cache_creation.ephemeral_5m_input_tokens` / `…_1h_…`) ever appears — which decides
  whether those tokens are priced at 1.25x or 2x. So Hearth takes the split from there
  and nothing else.
- **The per-request rows are not always there.** `usage.iterations` gave the individual
  requests on every success and nothing at all on the budget stop. Hearth prices the
  individual rows where the CLI reports them and the per-model total where it does not,
  which is sound here only because this schedule has no long-context tier (below):
  a total costs exactly what the requests behind it cost. **Amended 2026-09-09 by #147:
  they are not only sometimes absent, they are sometimes partial** — a session that
  called a tool billed two requests and reported one row — so rows that do not add up
  to the model's total are a partial view rather than a contradiction, and the total is
  priced. See "The bridge" below.
- **A session spends more than one model.** `modelUsage` named both `claude-opus-5` (the
  turn) and `claude-haiku-4-5-20251001` (the CLI's own housekeeping, ~900 input tokens) in
  every recording, and `total_cost_usd` is the sum of both. The pinned-model check is
  therefore about the *turn's* model — the session's own `init` event and every
  assistant message — and the second model is priced at its own published rates, not
  excluded and not charged as the pinned one.
- **Every observed cache write landed in the one-hour tier.** `cache_creation` reported
  `ephemeral_1h_input_tokens` and a zero five-minute count in all three sessions.

### The price schedule, and its arithmetic checked against the CLI's own

Read 2026-09-09 from <https://platform.claude.com/docs/en/about-claude/pricing>, per
million tokens:

| Model | Base input | Cache read | 5m cache write | 1h cache write | Output |
| --- | --- | --- | --- | --- | --- |
| `claude-opus-5` | $5 | $0.50 | $6.25 | $10 | $25 |
| `claude-haiku-4-5` | $1 | $0.10 | $1.25 | $2 | $5 |

**There is no long-context tier, and that was confirmed rather than assumed.** The
pricing page's long-context section says Claude 4.6 and later carry the full
one-million-token context window at standard pricing — "a 900k-token request is billed
at the same per-token rate as a 9k-token request". The Codex schedule's doubling above
272,000 tokens has no counterpart here, and its absence is what makes a per-model total
priceable.

The recorded success is the proof that the table is the one the CLI bills on:

| Line | Tokens | Rate | Dollars |
| --- | --- | --- | --- |
| opus input | 2 | $5 / MTok | 0.00001 |
| opus 1h cache write | 3,552 | $10 / MTok | 0.03552 |
| opus output | 4 | $25 / MTok | 0.0001 |
| | | CLI's `modelUsage["claude-opus-5"].costUSD` | **0.03563** |
| haiku input | 900 | $1 / MTok | 0.0009 |
| haiku output | 10 | $5 / MTok | 0.00005 |
| | | CLI's `modelUsage["claude-haiku-4-5-…"].costUSD` | **0.00095** |
| | | CLI's `total_cost_usd` | **0.03658** |

Hearth computes those three numbers itself, in hundredths of a microdollar so that
$6.25 and $1.25 stay integers, and settles only when its own arithmetic matches the
CLI's — per model and in total, within one microdollar per priced row. Any wider
disagreement leaves usage unknown and the resident's hold in place.

### How a run is launched

Measured 2026-09-09, and the shape `integrations/claude/config.py::session_command`
builds:

| Question | Observed |
| --- | --- |
| the prompt on stdin instead of in argv | accepted; identical stream and result. Hearth uses stdin, so a large pinned context can never meet an argv limit |
| `--max-budget-usd 0.25` | accepted as decimal dollars; Hearth writes the run's reserved microdollars as `f"{n / 1_000_000:.6f}"`, which is exact |
| `--tools ""` with no `--mcp-config` | `init` reports `"tools": []`; a run has no tools at all until the bridge grants some (#147) |
| event types beyond `system` / `assistant` / `result` | `rate_limit_event` appeared in every session. The parser counts and skips what it does not know, and settles only on the one `result` event |

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
`claude_subscription_binary_changed` — writing nothing when it does.

A store configured for Claude now admits, dispatches and settles work. A run is a
detached worker holding an inherited flock over its own folder; it launches the CLI once
and never again, keeps the CLI's original stream-json as the receipt, and settles from
it under the pinned schedule above. Cancellation signals the worker's own process group
and claims zero usage only when the launch provably never happened.

**What a Claude store runs.** Everything a Codex store does. A run pinned to reach
Hearth's own tools — its resident holds an enabled grant, declares `memory_writable`,
works a letter or holds post — is admitted, and the tools reach the session over the
bridge measured below. Karen holds a grant, so a household bootstrapped on Claude can
run Karen.

## The bridge — Hearth's own tools inside a session

Measured **2026-09-09** by the slice that built it (#147), with one real session of
about $0.046 against the pinned CLI. The shim was the real one
(`hearth.integrations.claude.mcp_bridge`); the trusted half was a throwaway socket
server, so no store was touched. The session ran from `$WORK` with the bounded flags
above plus:

```sh
--mcp-config <file> --tools mcp__hearth__hearth_journal_write \
  --allowedTools mcp__hearth__hearth_journal_write --max-budget-usd 0.200000
```

| Question | Observed |
| --- | --- |
| does the CLI launch and speak to a plain stdio MCP server of ours | yes: `initialize` (protocol `2025-06-18`), the `notifications/initialized` notification, then `tools/list`, all as newline-delimited JSON-RPC on stdin/stdout |
| what the session's `init` event says about it | `"mcp_servers": [{"name": "hearth", "status": "connected"}]` and `"tools": ["mcp__hearth__hearth_journal_write"]` — exactly the offered names, nothing else, and **before** the first model turn |
| is the tool really called through the shim | yes: an `assistant` message with a `tool_use` block naming `mcp__hearth__hearth_journal_write`, the shim's forwarded frame on the socket, a `user` event carrying the `tool_result` back, then the answer. `permission_denials: []` |
| what identifies one call | the JSON-RPC request id of the `tools/call` (`"2"` in this session), unique within the session; Hearth uses it as the call id `management_calls` records |
| `claude mcp list --mcp-config <file>` as a cheaper health check | **not usable**: `mcp list` does not accept `--mcp-config`, and with the flag before the subcommand it silently health-checked the *machine's* own servers instead |

The stream of that session is committed, scrubbed, as
`tests/integrations/claude/fixtures/management.jsonl`, and the checks in
`mcp_bridge.check_session` are written against it.

### The one measurement that changed a settled number

**`usage.iterations` is a partial view of the requests, not the list of them.** This
session billed two requests — the turn that called the tool, and the turn that answered
after it — and reported **one** row:

| Account | input | output | cache read | cache write (1h) |
| --- | --- | --- | --- | --- |
| `usage.iterations` (one row) | 2 | 3 | 3,947 | 133 |
| `modelUsage["claude-opus-5"]` | 4 | 69 | 3,947 | 4,080 |
| the two `assistant` messages' own `usage` | 2 + 2 | 33 + 1 | 0 + 3,947 | 3,947 + 133 |

#146 read a mismatch between the rows and the model's total as the stream
contradicting itself, and priced nothing. On these numbers that rule would have left
**every** session that calls a tool — which is every management run — with unknown
usage and its resident on hold. So rows that do not add up are now treated as what they
are, a partial view, and the model's own total is priced instead. That is sound for the
same reason the budget stop's total is: this schedule has no long-context tier, so a
total costs exactly what the requests behind it cost. The cross-check is unchanged and
is what actually guards the money — Hearth's own arithmetic over `modelUsage` came to
44,519 µ$ for `claude-opus-5` and 995 µ$ for `claude-haiku-4-5`, against the CLI's own
`costUSD` of $0.0445185 and $0.000995 and a `total_cost_usd` of $0.0455135.

The assistant messages remain the only place the five-minute/one-hour cache-write split
appears, and here they accounted for the model's whole 4,080 written tokens.

### How the bridge is built on that

- **The shim carries nothing.** `python -I -m hearth.integrations.claude.mcp_bridge
  <socket>` with an environment of `PATH` alone. No database, no owner token, no
  configuration directory, no credential — it forwards `tools/list` and `tools/call`
  over a unix socket in the run's own folder and hands back the reply, one connection
  per call.
- **The socket is the run's.** `<run folder>/bridge.sock`, created 0600 inside a 0700
  folder, with every connection's peer uid checked from the kernel (`SO_PEERCRED` on
  Linux, `LOCAL_PEERCRED` on macOS) before a byte is read. A run folder under a deep
  temporary path exceeds `sun_path` (104 bytes on macOS), so a long address is bound
  and connected relative to its own directory — the same file, named more briefly.
- **The trusted half answers in the worker.** `management.bridge` authenticates with
  `BoundRun` and mutates and audits in one transaction, exactly as
  `codex/management_runtime.py` does over the app server's `dynamicTools`. The socket
  is watched in the worker's own read loop, so nothing runs concurrently with the
  transaction a call opens.
- **The session is checked before it is trusted.** The `init` event has to name
  Hearth's server as connected, exactly the offered tools, the pinned model and the
  pinned build, or the session is stopped — `claude_tools_changed` or
  `claude_session_unpinned` — before its first turn can reach a tool. Until that check
  passes, a call is refused `management_session_untrusted`.
- **The pins.** `catalog_sha256` digests the pinned build and model, `tools_sha256` the
  run's own tool list under the same function Codex digests it with; both are written
  into `run_management` at `start` and into the receipt by the worker, and settlement
  refuses `management_configuration_changed` if they disagree.
- **A bridge that fails ends the session.** `mcp_bridge_failed` is recorded in the
  receipt and the session settles as failed with whatever it spent — never as unknown
  with a relaunch.
