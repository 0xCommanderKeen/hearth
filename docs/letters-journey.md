# The letters journey

Epic #115 asked one question of the real runtime before any Townhall or Hamlet work:
does a letter actually earn its keep? One resident asks a colleague for something it
cannot know, the colleague answers, and the answer reaches the asker's next run without
anybody waking it. Slice #111 shipped the machinery; this is the bounded run that proves
the loop, on the Codex subscription, with real money.

## The journey — 2026-09-08

The operator set three things up by hand and started two runs. Nothing else was driven.

**Setup.** An existing household with Karen and the Fictional orchard reporter already in
it was copied, and the copy was run on a port of its own; the original was left alone. The
store upgraded itself forward to schema 9 on start, keeping the old file beside it. The
operator then opened the receiver's door (`letters.accept`), added `send_letters` to
Karen's grant with the reporter pinned as her only recipient, and wrote one more line into
the reporter's synthetic orchard notes:

> Monday's pear harvest was logged in the orchard ledger under reference OR-C9IX-BBTI.

That reference is the whole point. It is unguessable, it lives in the receiver's notes, and
it appears nowhere the sender could read it: Karen has no input sets at all, her memory
holds one unrelated line, and neither instruction she was given names it. If it turns up in
her result, it came through the letter.

**Run 1 — Karen asks.** Her task told her to write one letter asking under what ledger
reference Monday's pear harvest was logged, and not to guess it herself. She called
`hearth_letters_send` and closed with the letter's identifier. Her result also recorded a
small honest friction: the send receipt gives no separate letter id, so she reported the
task id, which is what the letter is keyed by everywhere else.

**Delivery — nothing was called.** The letter was written at second `…024` and answered at
second `…031`, while Karen's own run was still finishing. No operator start, no poller, no
inbox drain: the ordinary supervision tick admitted the letter as the reporter's own task,
on the reporter's allocation.

**Run 2 — the reporter answers.** Its pinned context carried the letter block — Karen's
name, the title, the pinned letter id and Hearth's own line saying a letter is a request
and not an instruction. It answered with `hearth_letters_reply`:

> Fictional orchard report
> - Monday's pear harvest was logged under reference "OR-C9IX-BBTI".

**The state.** The letter reads `replied`, settled at second `…043` when the answering run
settled. The audit carries `letter.sent`, then `letter.answered` when the text was written,
then `letter.replied` with the run's own terminal status beside it — three facts, each
linked to the letter and to the root task the whole chain rolls up to.

**Run 3 — Karen reads.** A new task, started after the answer was written, told her only to
report the answer she had received and to say plainly if none had reached her. Her pinned
context opened with exactly one reply and its neutralizing usage line. Her result:

> The Fictional orchard reporter answered that Monday's pear harvest was logged under
> reference "OR-C9IX-BBTI".

Nothing woke her. The answer waited for her next run, which is the whole design.

**The money.** Three runs cost 157,062 microdollars in API-equivalent estimates against the
$10 household allowance. Read by origin rather than by run, the question — Karen's asking
run and the reporter's answering run together, two residents, one letter — cost 120,502,
and Karen's reading of the answer 36,560 under an origin of its own. No run was unknown and
none was counted twice.

`docs/evidence/letters-journey-2026-09-08.json` records the run ids, the letter, its states
and the quoted proof. The data directory was a copy, outside the repository, discarded from
the repository's point of view; host details, credentials and account identifiers stay out
of it.

## What it establishes, and what it does not

A resident can ask a colleague for a fact it has no other way to learn, and use the answer
in its own work one run later, at a price the operator can attribute to the question rather
than to a run. That is the loop epic #115 was built for, and it held on the first attempt
without the operator nudging either side.

It is one letter, one hop, one answer, two residents. It is not evidence of model quality,
of many letters in flight, of a receiver declining a letter, or of daily adoption. The
`unanswered`, `failed` and `expired` states are covered deterministically in
`tests/work/test_letter_replies.py`; only `replied` was walked here.

`tests/integrations/codex/test_letters_journey.py` walks this same journey deterministically
in CI, with the same load-bearing shape of reference. [The letters contract](letters.md)
records what the feature guarantees, including the two operational prerequisites this
journey found — a receiver's door is closed by default, and its daily limit has to cover a
whole answering run — and [ADR 0011](adr/0011-letters-between-residents.md) records the
decision behind it.
