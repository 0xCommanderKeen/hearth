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
or real-host isolation. Fixture names and output explicitly say they are simulated.

The journey records four runs: one manager, two serial validation cases and one
report. It checks the causal links between the manager's task and native binding,
seven operation receipts, immutable candidate/publication, evaluator case inputs and
results, creator/current manager, exact skill and input pins, routine occurrence,
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

## Remaining real acceptance

The first approved authenticated Mac Codex-subscription attempt finished with one
fully accounted manager run costing 150512 microdollars ($0.150512), with no authoring
mutations. Two draft-save requests supplied six required phrases where the schema
permits four; the generic invalid-arguments reply did not identify that limit. This
does not complete the creation, authoring or delivery gate.

Schema validation refusals now retain their error code and include bounded field
paths and schema limits, excluding submitted values and exception messages. A real
Bridge/temporary-SQLite regression verifies rejection without catalog changes,
durable refusal replay, and a corrected request using the same operation ID with a
new call ID. Another checks bounded feedback with many errors and private values.
Tool and bootstrap guidance state phrase/note limits and missing-input notation.
This fix itself performs no provider retry; the standalone proof is superseded by
the integrated journey.

The prepared second attempt requires new explicit approval for four additional
starts, five across both attempts. It verifies the first attempt's closed gate and
known accounting before using a fresh directory, and subtracts its 150512
microdollars from the shared allowance. The first attempt and its verified held
backup remain preserved. A fresh directory never restores spent allowance.

The prepared private harness fixes the reviewed release, existing selected CLI/model
and login, one fresh data directory, fictional notes, three residents, concurrency
two, 48 native calls and a ten-minute start window. A thread-safe gate durably consumes
at most four distinct `runtime.start` allowances before delegating to the existing
adapter. A fifth distinct run is refused before provider launch; replay does not
restore or consume allowance. The gate closes before shutdown and cancellation
cleanup, including when unused allowance remains. The shared allowance remains at
most $10/day across all proof attempts, including known prior usage and unresolved
exposure; a new directory never resets it.

An unexpected failure, interrupted execution or terminal unknown usage preserves
its evidence and marks the proof incomplete. Only after terminal accounting does
the harness stop its own supervisor and capture a held restore. Actual report
meaning, Townhall/Residents/Hamlet views, restart identity and restored result/usage
reads still need verification on that real evidence. A synthetic screenshot or a
passing fixture never completes this gate. Stop at the working demo; production
deployment and personal-source connections are outside this task.
