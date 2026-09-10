# Runtime per resident

Status: accepted, 2026-09-09.

Hearth has had exactly one runtime per store since ADR 0008: `system_meta.runtime_kind`
named it, `create_app` built that provider's adapter, admission pinned it onto every run
and the executor refused to work a run pinned to anything else. ADR 0014 kept that shape
while removing the mocks, and #145 added a second live kind — `claude_subscription` — but
a household still had to choose one brain for everyone. The epic (#144) asks for Karen to
stay on Codex while another resident runs on Claude, in the same household, on the same
store. That is a persistence and authority change, so it is written down here.

**Decision.** Which runtime a resident runs on is a declaration fact.

- `declarations.runtime` (schema 11, nullable) is the runtime one resident's work is
  admitted to. Null means "the store's default", which is what every resident that has
  never chosen says, and what every row an upgrade fills says
  (`storage/migration.FILLS`) — because every existing resident really did run on the
  store's runtime. `system_meta.runtime_kind` keeps its name and becomes that default:
  the runtime a new store records, the one an unpinned resident runs on, and the one the
  quiet-store switch rule moves. Switching it still moves everybody who declared nothing
  and nobody who declared something.
- **Admission pins the resident's runtime, not the store's.** `runs.runtime_kind` is
  written once, at admission, from the declaration with the default as the fallback, and
  the price schedule pinned beside it is that kind's own registry entry. A finished run
  keeps both, whatever the resident or the household becomes afterwards (ADR 0013): the
  pin is the honest record of where the work happened.
- **One instance, several runtimes.** `create_app` builds the adapter for the store's
  default and, beside it, every other live runtime whose configuration is present, each
  through the registry's own `module` / `runtime` entry rather than by name. The executor
  holds them as `{kind: Runtime}` and works each run with the one its own pin names. The
  single-runtime host is the one-entry map, and the store's default must be one of them
  or the whole pass refuses `runtime_store_mismatch` — an instance that cannot work what
  its store admits next would queue work nothing is ever going to start.
- **A second runtime that will not open leaves the household standing.** The default is
  built or the instance refuses by name, because that is the runtime the store's own work
  needs. Every other runtime is a second brain some residents use, so a lapsed login or a
  CLI that updated past its pin leaves *that* runtime out of the map rather than taking
  the household down with it. Each runtime a resident declares and this instance could
  not open is recorded once at start as `runtime.unavailable`, with the provider's own
  refusal where there was one and `runtime_not_configured` where nothing was pointed at
  it, so the reason lives in the store rather than in somebody's terminal.
- **A declaration may only *move* to a runtime this store has been configured for.** The
  check is on the change, not on the value: `save_resident_in_transaction` refuses
  (`runtime_not_configured`) a runtime that differs from the one the resident declares now
  and is either not live in this release or not one this store has really had. Keeping
  what already stands is always allowed, so a resident whose kind a later release retires
  can still be renamed, repaused and reconfigured instead of becoming unsavable — the same
  reason `declarations.runtime` carries no CHECK. "Really had" is the binary pin that
  runtime writes the first time it is configured, which is the store's own record that the
  provider was there — binary, version and login all checked before it was written — with
  the store's own default always answering yes, because that is what the store records it
  runs. Asking the running process instead would make one stored declaration mean
  different things in different instances.
- **A run whose pinned runtime is not configured here waits.** It is never handed to
  another provider — that would be a different run against the same reservation — and it
  is never thrown away either, because the usual cause is a configuration this machine is
  missing rather than a provider the household has lost, and work is not destroyed over
  an environment variable. A run that was never launched is left exactly as it is, still
  waiting to start, and is worked as soon as the runtime is configured again; an operator
  who wants it gone cancels it, and it then settles at zero through the ordinary path, on
  a receipt the registry builds from the store's own pins rather than from the provider.
  A run that was already launched is held `interrupted` (or `stopping`, if cancellation
  was asked for) with a `runtime_unavailable` audit fact: it may really have spent money,
  a priced run settles from its own provider's receipt, and there is no receipt for a
  session this instance cannot see. Nothing is retried and nothing is relaunched either
  way.
- **The execution profile is the resident's runtime.** It always named a runtime kind;
  now it names the resident's own. Provisioning offers every runtime the store is
  configured for and writes the chosen one into the declaration — the default is written
  as null, so those residents still travel with a quiet-store switch. A management grant
  may name any configured runtime rather than only the store's default, and the runtime
  that has to be granted for a skill example is the requesting resident's own, because
  that is where the example runs.
- **A bundle carries the runtime as definition** (ADR 0010). Import keeps it where the
  target store has that runtime, and otherwise creates the resident on the store's own
  default with `runtime_not_configured` in the resolution beside the requested kind. The
  bundle is definition, not a claim on the importing household, and a resident is worth
  more than the brain it was written against.

**Consequences.**

- `execution/`, `work/` and `management/` still know no provider names. They read the
  declaration's runtime, the store's default and the registry; the two live adapters are
  named in one place, `integrations/interface.RUNTIMES`, and reached through it.
- Two residents in one household can now spend against two different price schedules in
  the same day. Each run's schedule is pinned at admission from its own kind, so the
  household budget is still one number and every run in it still settles under a schedule
  someone can check.
- The issue asked for a run on an unconfigured runtime to *settle* as unknown. It does
  not: settling a priced run means writing a receipt, and Hearth has none for a session
  it cannot observe. Unknown execution is visible and never evidence that retrying is
  safe, so such a run waits instead — and a wait is also what keeps a household from
  losing a day of work to a missing environment variable.
- There is no `pending` resident state. The issue suggested one for a bundle naming a
  runtime the instance lacks, modelled on the chat routes of #137, which are not built
  yet. Rather than invent one, import records the substitution in the resolution the way
  it already records a substituted profile, and a resident that really does declare a
  runtime this instance has lost is visible through its own waiting runs.

Implemented by #148, in epic #144.
