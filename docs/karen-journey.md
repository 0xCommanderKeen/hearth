# Karen's bounded reporting journey

The integrated deterministic demo starts with one plain-language task. Karen authors
a reporting skill, executes its normal and missing-input examples, publishes the
passing candidate, creates a reporter, assigns the exact published revision and a
named fictional input, schedules a daily 09:00 Europe/Ljubljana routine, and submits
its first report. The saved report preserves two facts: 12 pears harvested Monday
and 3 trees planted Tuesday.

## Deterministic evidence

`tests/integrations/codex/test_karen_journey.py` uses an explicitly scripted CLI
fixture with the ordinary `CodexLiveRuntime`, detached workers, native structured
callbacks, supervisor, SQLite owners, saved artifacts and usage accounting. The
fixture has no database access and consumes the same pinned reader context as the
subscription adapter. No real CLI, login or provider is used by these tests. The
subscription runtime kind deliberately exercises that adapter's receipt and backup
paths; its synthetic counters and passed assertions are not evidence of model quality
or real-host isolation. The fixture's names and output say plainly that they come
from a scripted CLI.

The journey records five runs: two manager turns — Karen authors and requests, then
deploys once the evidence is in — two serial validation cases on Karen's own slot, and
one report. It checks the causal links between the manager's task and native binding,
eight operation receipts, immutable candidate/publication, case inputs and results,
creator/current manager, exact skill and input pins, routine occurrence,
saved report and one settlement per run. Children receive no management authority.

Reply-loss checks discard a committed reply before the scripted journey advances
at publication, resident creation, skill assignment, routine/input configuration and
the first work assignment. Exact call replay and the same operation under a fresh
call ID return stable receipts; changed operation payloads refuse without effects.
Supervision and database/API owners restart while the original manager process stays
alive. A reconstructed bridge also reads the exact durable committed call receipt,
independently of the native transport's duplicate-response cache.

An incomplete native usage counter leaves Karen's completed result and the rest of
the chain readable while preserving a `usage_unknown` hold. Reopening and reconciling
does not launch another run or duplicate settlement. Current-data backup and a new
held restore retain the complete chain and refuse mutations. Backup checks exclude
operator tokens, auth files and private worker launch requests; required historical
run bindings remain intact under the existing restore contract.

`test_karen_contention.py` runs two real native protocol fixture processes against
one household. Both managers remain active while competing for one child slot. One
request starts and saves a report; the other receives the household concurrency
refusal. Unrelated resident requests are also refused. There is one child task and
one operation receipt, all three runs settle once, and admitted work is reconciled.

## Startup correction discovered by the journey

The first ordinary supervisor run reproduced a launch race: `start()` returned
before its detached worker acquired `worker.lock`, so immediate inspection reported
unknown execution and the management worker subsequently refused its inactive run.
The parent now acquires the lock before spawning and passes the same locked open
file description to the child. The child validates the inherited file and retains
ownership through startup and execution. This preserves visible running evidence
without sleeps or weakening management authorization. A gated OS-process regression
checks delayed startup, worker death, spawn failure and invalid handoff.

## Authenticated acceptance

The bounded authenticated journey passed using synthetic orchard notes. Karen
saved a draft, ran the ordinary and missing-input examples, published the exact
passing revision, created a reporter with its pinned skill and named input, set a
daily routine, and delivered the first saved report. The ordinary example preserved
the supplied relationships; the missing-input example honestly reported absent
notes. The actual report preserved the supplied harvest and planting facts.

Independent owning-record checks verified publication and validation provenance,
creator and provisioning receipts, exact revision and input pins, routine timing,
saved output hashes and known accounting. Current-data backup verification and a
new held restore preserve that chain. The held API rejects mutations. Actual
Chromium desktop/mobile inspection passed for skill content, both passing examples
and users, resident configuration and origin, scheduled occurrence, the saved report
and Hamlet, with no overflow or browser errors.

Earlier bounded attempts exposed opaque argument feedback and a self-authored
output ceiling shorter than its required phrase. Schema refusals now give bounded
trusted field/limit hints without echoing inputs; the checker continues to reject
invalid output assertions. Known validation failure may be corrected through the
normal revision workflow; uncertain execution never authorizes another run. Prior
attempts and accounting remain preserved privately and do not reset the shared
allowance.

The manager returned slash-form run URLs. Both `/#runs/<id>` and `/#run-<id>` now
open through the same authenticated reader, while saved output remains immutable
plain text. This frontend correction does not require repeating model execution.

Detailed host evidence, credentials and private accounting stay outside the
repository. Provider calls remain outside CI. This establishes the bounded demo,
not general model quality or daily adoption; production deployment and personal
source connections remain outside its scope.

## The journal journey — 2026-09-07

Epic #126 asked for one more bounded real run: the Fictional orchard reporter's second
daily run referring to what its first run wrote. The operator created the orchard input
set, set the shared library up, and provisioned one writable reporter with the "Keep a
journal" skill it receives at provisioning; four ordinary daily reports then ran on the
real subscription against the same two fictional notes.

Runs 1 and 2 each closed with their own entry, unprompted, and both said plainly that no
memory update was needed — the etiquette working, not a shortage of facts. Run 2 opened
with entry 1. The operator then asked the reporter to begin each report by saying what its
journal records it reported last time. Run 3 opened with entries 2 and 1 and named the
previous run by its identifier, which appears nowhere in the notes, the instruction or the
memory: it could only have come from the entry admission pinned to it. Run 4 received a
standing preference in its task, saved it as memory revision 2 recorded with
`author='run'`, kept the operator's existing line, and said in its own entry which revision
it wrote.

Four runs cost 168,444 microdollars in API-equivalent estimates against the $10 household
allowance. `docs/evidence/journal-journey-2026-09-07.json` records the runs, the entries
and the quoted report. The data directory was fresh, outside the repository, and discarded
after recording; host details, credentials and account identifiers stay out of it. This
establishes that a resident's own runs write its journal and memory and that a later run
opens with them. It is not evidence of model quality or daily adoption.
