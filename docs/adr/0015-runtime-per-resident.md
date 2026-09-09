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
- **A declaration may only name a runtime this store has been configured for.** Two
  gates, because they answer different questions. `Declaration.validate` refuses a kind
  this release does not ship live (`runtime_not_configured`): a retired kind is history a
  finished run carries, never a brain to admit new work to. `save_resident_in_transaction`
  refuses a live kind this store carries no binary pin for, with the same code: the pin a
  runtime writes the first time it is configured is the store's own record that the
  provider was really there — binary, version and login all checked before it was
  written — and the store's own default always answers yes because that is what the store
  records it runs. Asking the running process instead would make a stored declaration mean
  different things in different instances.
- **A run whose pinned runtime is not configured here is never handed to another
  provider.** One that was never launched is asked to stop, and the executor settles it
  at zero from the registry's cancellation receipt, which needs the store's pins and not
  the provider — exactly how a run whose pinned context a release cannot rebuild ends
  (`storage/migration._release_unlaunched_runs`). One that was already launched stays
  interrupted with a `runtime_unavailable` audit fact: it may really have spent money, a
  priced run settles from its own provider's receipt, and there is no receipt for a
  session this instance cannot see. It settles when the runtime is configured again, or
  the operator ends it. Nothing is retried and nothing is relaunched either way.
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
  safe, so such a run waits, visibly, exactly as every other unobservable run does.
- There is no `pending` resident state. The issue suggested one for a bundle naming a
  runtime the instance lacks, modelled on the chat routes of #137, which are not built
  yet. Rather than invent one, import records the substitution in the resolution the way
  it already records a substituted profile, and a resident that really does declare a
  runtime this instance has lost is visible through its own waiting runs.

Implemented by #148, in epic #144.
