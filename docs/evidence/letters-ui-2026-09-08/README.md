# Letters in Townhall and Hamlet — screenshots, 2026-09-08

Captured for #112 against a fake-backed Hearth: a temporary data directory outside the
repository, `tests/fake_runtime.py` in place of the Codex subscription, on a spare port.
No live household, no credentials and no real spending are involved.

Every row behind these pages was written through the interface that owns it — the letter
tools on a real native bridge, the operator letters API, the reply tool, the letter
settlement the executor's own transaction runs — so what is shown is the projection under
test rather than hand-written fixture JSON.

The household: Karen (granted `send_letters` by her own setup), a Fictional orchard
reporter with an open door and a $0.05 day, and a Gardener whose door has never been
opened. Karen asks the reporter one question and is refused a second letter to the
Gardener; the reporter answers; the operator writes a third letter of its own.

| File | What it shows |
| --- | --- |
| `1-letters-section.png` | The Letters section on a resident page: what reached it, each in one named state, with the answer and the state of its door and day. |
| `2-send-a-letter.png` | The answer and the link to the run that wrote it, an empty Sent lane, and the operator's own Send a letter form. |
| `3-shut-door.png` | A resident that accepts no letters, said where letters are listed rather than discovered by writing one. |
| `4-lineage-and-refusal.png` | Task view: `root → this task` breadcrumbs with sender names and state chips, and a run carrying the letter it was refused. |
| `5-hamlet-walk.png` | Hamlet with the post it walked listed under the village; the walk itself is drawn from these events and from nothing else. |
| `6-lineage-mobile.png` | The breadcrumb at 390px. No horizontal page overflow. |
