# Hearth

A home for agents that do useful work. Persistent residents have purpose, memory,
and enforced permissions. Hamlet shows their real activity; Townhall provides
operator controls in the same application.

Hearth is a standalone project, starting with a new read-only daily-summary
Reader and fresh data. Residents run on a real subscription — Codex or Claude, as
each resident declares; the notes they read are synthetic.

## Current implementation

The Python core persists resident revisions, deduplicated task commands, run
admission, and budget reservations. Each change commits with its audit record in
one SQLite transaction. Two runtimes execute work: a bounded, read-only run on the
Codex subscription or on the Claude subscription, whose provider receipt settles the
run's cost and output. Which one a resident runs on is its own declaration, so one
household can hold both, and Townhall names the runtime that worked each run. No live
source files are used. Hamlet and Townhall share one browser application, an
authenticated client, and the authoritative snapshot stream.

```sh
uv sync --frozen
make check
```

Python 3.14, uv, Node 22.22+, and pnpm 11.22 are required. The backend uses FastAPI;
the browser uses React and TypeScript. `make check` builds one Python wheel that
contains the compiled browser, then installs it with locked runtime dependencies
in a temporary environment and exercises its CLI and loopback HTTP application.
See the [standalone installation steps](docs/release.md).
Money is represented as integer microdollars; reservations are admission policy,
not a provider-enforced billing ceiling.

Hearth stores its local state in ignored `.hearth`. Use `HEARTH_DATA=/some/path`
for a separate instance. A store from an older Hearth release is upgraded on start
and the original is kept beside it as `hearth.db.before-v<N>`; a file that is not a
Hearth store is refused untouched. A store recorded against a runtime this release
no longer ships adopts a live one as its default on start; its finished runs keep
their own pin, and work a removed runtime left in flight ends as cancelled with usage
unknown.

## Open the application

Build with `make check`, set `HEARTH_OPERATOR_TOKEN` to a local operator credential
of at least 16 characters, point `HEARTH_CODEX_BINARY` at the pinned Codex CLI and
`HEARTH_CODEX_AUTH_HOME` at its logged-in `CODEX_HOME`, then start:

```sh
uv run uvicorn hearth.app:from_env --factory --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766` and enter that token. Hearth starts empty and ships no
sample data: create a resident, assign it a task, and open its summary. The
credential is remembered in this browser across restarts until you select Lock.

The Claude subscription is configured beside it, with `HEARTH_CLAUDE_BINARY` pointing
at the pinned Claude Code CLI and `HEARTH_CLAUDE_CONFIG_DIR` at its own logged-in
configuration directory (never the machine's `~/.claude`); see
[the Claude runtime](docs/claude-runtime.md) for the pins, the one-off login step and
the measurements behind them. A host set up for both starts with both sets, and any
resident may then declare either one:

```sh
HEARTH_OPERATOR_TOKEN=... \
HEARTH_CODEX_BINARY=/path/to/codex HEARTH_CODEX_AUTH_HOME=/path/to/codex-home \
HEARTH_CLAUDE_BINARY=/path/to/claude/versions/2.1.263 \
HEARTH_CLAUDE_CONFIG_DIR=/path/to/private-claude-config \
uv run uvicorn hearth.app:from_env --factory --host 127.0.0.1 --port 8766
```

`GET /health` needs no token and answers with the runtimes this instance opened;
`GET /api/health`, behind the operator token, adds any it was pointed at and could
not open — a lapsed login, a CLI past its pin, half a configuration — with the
provider's own reason. The store's own default has to open or Hearth refuses to
start; a second runtime that will not open leaves the household running and only
its own residents' runs waiting.

Where those sessions execute is `HEARTH_SANDBOX`: `process` (the default) runs the CLI
as a child of Hearth's worker, which is what a development host does, and `container`
runs it inside a container created for that run, from the image
`HEARTH_SANDBOX_IMAGE` pins by digest on the network `HEARTH_SANDBOX_NETWORK`, reached
through the client `HEARTH_SANDBOX_DOCKER` names and, where the daemon is not the
client's own default, `HEARTH_SANDBOX_DOCKER_HOST`. See
[the sandbox](docs/sandbox.md) for what it pins, what it refuses and what a real
container runtime was measured to do with it.

On a server that is a whole deployment rather than two variables: `deploy/` holds
Hearth's own image, the sandbox image, one compose file and the packet filter that makes
the sandbox network what it claims to be, and [its runbook](deploy/README.md) is the
order to do them in. There, `HEARTH_SANDBOX_SHUT` and `HEARTH_SANDBOX_OPEN` name what a
session must not and must be able to reach, and Hearth measures it from inside that
network at every start rather than taking the network's name for it.

Use `HEARTH_DATA` to select a separate data directory; the default is `.hearth/local`.
For browser development, `pnpm dev` from `web/` proxies its `/api` requests to the
same local backend. Continuous integration has no subscription, so the test suite
and the installed-wheel smoke inject the fake runtimes in `tests/fake_runtime.py`;
they are never packaged.

See the [implementation gates](docs/implementation.md),
[project plan](docs/rebuild-plan.md), and [domain glossary](CONTEXT.md).
