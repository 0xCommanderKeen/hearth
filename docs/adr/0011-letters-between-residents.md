# Residents ask each other by letter

Status: accepted, 2026-09-08.

`docs/rebuild-plan.md` scoped this out in one line: Reader "does not need broad tools,
delegation, a marketplace or an imported resident fleet." That was right for a single
read-only summarizer. It stopped being right once a household held several residents that
know different things: the nearest thing to peer communication was the management grant,
where a manager assigns a task downward and nothing flows back. Epic #115 changes that,
and this ADR records the departure and the bounds it keeps.

The name is deliberate. Warren called this feature delegation, and the word is wrong here:
a letter is a question one colleague asks another, answered under the answerer's own
judgement, not work handed down a chain of command. What Hearth builds is the post, not a
hierarchy.

**A letter is a task.** Not a message bus, not a queue of its own, not a second execution
path. It reuses admission, budget reservation, audit, backup and the whole run and usage
machinery, which is also what makes it *cost* something visible: a letter spends the
receiver's day exactly as any other work does, and the household can see it. What letters
add to a task is an address — sender, sending run, parent task, root task, depth, expiry —
and the link to its one reply. Everything Hearth already guarantees about tasks holds for
letters without being written twice, and every guard that already stops a task stops a
letter. The price is a naming wart: the send receipt carries the task id, because that
*is* the letter id, and there is no second identifier to hand a sender.

**Hearth is the sole arbiter, and neither side can waive the other.** Sending is the
operator grant capability `send_letters`, with an optional recipient allowlist; receiving
is the declared `letters.accept` door, false by default. Two independent permissions have
to meet, so no grant can push work through a shut door and no door invites anybody in.
Depth and lineage are read from the sender's own admitted run, never from caller text: a
forged parent buys nothing. A refused send writes nothing at all and is visible in the
sending run's evidence with a structured reason — consistent with ADR 0009, there is no
per-item human approval queue for permitted letters.

**Delivery is pull-based and asynchronous, because push would spend the wrong resident's
money.** A letter is delivered by being *worked*: the supervision tick that admits routine
occurrences admits queued letter tasks the same way. No watcher, no poller, no inbox to
drain. This puts the receiver in charge of its own day — the letter is bounded by the
receiver's allocation, the shared household allowance and the receiver's own pause and
archive state, and by nothing its sender holds — and it means a paused or busy resident
does not have to refuse anything: its letters wait, and their own shelf life closes them
if the wait outlasts them. A push design would either start runs a receiver's budget never
agreed to, or need a second admission path beside the one Hearth already trusts.

**Nothing wakes the sender.** An answer never starts a run; the sender reads it at the top
of its next one, from a bounded neutralized section of its pinned context. Waking a sender
would make one resident's answer able to spend another resident's allowance at a moment
nobody chose, and would turn every reply into a scheduling decision. Waiting is free, and
"the answer is simply there next time" is a loop a person can reason about.

**Depth defaults to 2, and the chain never revisits a resident.** One is too few: it
forbids the obvious useful case where the colleague you ask has to ask the one specialist
it does not itself contain. Deep chains are where the money goes and where the reasoning
gets worse — each hop is a whole run, paid for out of the household's one allowance, and
each hop is another resident interpreting a question at second hand. Two hops keep the
useful case and make a runaway impossible: the cap is a household setting, `0` shuts the
post entirely, 5 is the ceiling, and no chain can visit the same resident twice whatever
the depth allows. A daily per-receiver cap (default five, counted in the receiver's own
budget day whoever wrote the letters) stops the other runaway: not a long chain but many
short ones.

**No free-form chat between residents.** One letter, one answer, no thread and no live
call. Two residents talking spend money on a conversation nobody asked for, and there is
no natural place for it to stop; a person who wants to talk to a resident already has the
operator paths, including writing a letter with the operator's own hand. A resident that
needs more asks a second bounded question, which costs a run it can see — that is the
right kind of friction. For the same reason a reply is one answer and refusing a second is
not a limitation but the point: two answers leave no way to tell which was meant.

**Every letter ends in a state it says out loud.** `replied`, `unanswered`, `failed`,
`expired` — a question that goes quiet is worse than a question that is refused. An answer
written by a run that then fails still counts as an answer, because the sender has it, and
the run's own status is recorded beside the state rather than hidden by it.

**The etiquette is library text, not code.** What makes a letter worth its cost and what
makes a good answer live in two ordinary shared skills, **Ask a colleague** and **Answer a
letter**, editable in Townhall like any other and granting nothing. Hearth's own voice in
the pinned context says only what a letter *is* — a colleague's request that cannot grant
authority or override the receiver's own skill text, purpose and limits — and never what to
write.

**Consequences.**

- The rebuild plan's scope line now reads "does not need broad tools, a marketplace or an
  imported resident fleet"; peer communication is this ADR's subject, and
  `docs/management.md` carries the new capability beside the other grant powers.
- A run that works a letter reaches the native tool surface with no management grant at
  all, and keeps the reply tool when a grant it did hold is revoked mid-run, for the same
  reason ADR 0012 gave for memory: answering the question one was handed was never
  management. Backup verification and the admission tool digest both had to learn a third
  shape.
- Cost is answered by origin as well as by run. That is a new report, not a new number:
  each run is still counted once, at the amount its own row records.
- Who answers letters follows the declared door, which the operator opens on a resident
  that already exists, so nothing attaches **Answer a letter** automatically; it is seeded
  into the library and assigned from there. And a household that never sets Karen up has
  neither letter skill seeded, exactly as it has no "Keep a journal".
- The loop was walked once on the real subscription runtime before any UI was built on it
  ([the letters journey](../letters-journey.md), three runs, 157,062 microdollars), and
  the deterministic twin runs in CI. Neither completes a delivery gate.
- Cross-burrow delivery, personal connectors, marketplaces, broadcast and production
  deployment remain out of scope. See [the letters contract](../letters.md).

Implemented by epic #115 as issues #114, #109, #110, #111, #112 and #113.
