# Hearth

A home for agents that do useful work. Persistent residents have purpose, memory,
and enforced permissions. Hamlet shows their real activity; Townhall provides
operator controls in the same application.

Hearth is being built as a private successor to Warren. The first workflow is a
new read-only daily-summary resident. Existing residents remain on Warren while
the lifecycle and migration procedures are proven.

## Current implementation

The Python core persists resident revisions, deduplicated task commands, run
admission, and budget reservations. Each change commits with its audit record in
one SQLite transaction. A deterministic mock runtime produces a simulated summary
and exercises recovery, cancellation, and accounting. No real agents, model calls,
or live source files are used. The browser UI is next.

```sh
uv sync --frozen
make check
uv run python -m hearth demo
```

Python 3.14 and uv are required. The initial core has no production dependencies.
Money is represented as integer microdollars; reservations are admission policy,
not a provider-enforced billing ceiling.

The demo stores its local state in ignored `.hearth/demo`. Use `--data /some/path`
for a separate simulation. Every output and cost is labeled simulated. The mock
returns a fixed synthetic summary; it does not interpret arbitrary instructions.
Runtime scenarios include success, held execution, failure, and unknown usage for
deterministic recovery tests.

See the [implementation gates](docs/implementation.md),
[rebuild plan](docs/rebuild-plan.md), and [domain glossary](CONTEXT.md).
