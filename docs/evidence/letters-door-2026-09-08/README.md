# Turning the letters door from Townhall — screenshots, 2026-09-08

Captured for #172 against a fake-backed Hearth: a temporary data directory outside the
repository, `tests/fake_runtime.py` in place of the Codex subscription, on a spare port.
No live household, no credentials and no real spending are involved.

The household is the synthetic Reader, seeded through the same helper the test suite uses
and nothing else. Its door has never been opened, so the run below is the first thing that
ever writes it. Every state shown was written through the interface that owns it — the
operator declaration route — rather than hand-written fixture JSON.

| File | What it shows |
| --- | --- |
| `1-door-shut.png` | The door as it stands by default: Reader accepts no letters, said where letters are listed, with the control that opens it and what opening it does and does not grant. |
| `2-door-open.png` | One click later. The door is open, the same control now shuts it, and the receipt names the declaration revision that carries it — revision 2 — and that runs already admitted keep the revision they were pinned to. |
| `3-door-refused.png` | Reader was archived, then the door was turned. Hearth's refusal is shown where it was written — "The door did not move · resident archived. It stands as it did." — rather than as a door that quietly stayed where it was, and the door still reads open at revision 2 because that is what still stands. |

Desktop, 1440px. The control wraps rather than overflowing: measured at a 340px container
its content width equals its box width and it lays out on two lines.
