"""Synthetic fixtures for the test suite.

The product ships no demo or seeded data; the Reader example that used to live in
`hearth.inputs.demo` and `POST /api/demo/reader` now exists only here, so tests keep
a stable resident to exercise without the application carrying mock content.
"""

from hearth.inputs.catalog import Inputs
from hearth.inputs.selection import save_selection
from hearth.residents.models import Declaration
from hearth.work.service import Hearth

BUILTIN_INPUT = "synthetic-reader-notes"
BUILTIN_NOTES = [
    "Synthetic note: drafted the Hearth foundation.",
    "Synthetic note: task submission survives retries.",
    "Synthetic note: exercise cancellation and recovery next.",
]


def seed_inputs_in_transaction(hearth: Hearth, db) -> str:
    Inputs(hearth).save_in_transaction(
        db,
        "seed-synthetic-reader-notes",
        input_set_id=BUILTIN_INPUT,
        name="Synthetic Reader example notes",
        notes=BUILTIN_NOTES,
        actor="operator",
    )
    return BUILTIN_INPUT


def seed_reader(hearth: Hearth):
    """Idempotently create the synthetic Reader resident and its input selection."""
    with hearth.database.transaction(write=True) as db:
        if not db.execute("SELECT 1 FROM residents WHERE id='reader'").fetchone():
            input_set_id = seed_inputs_in_transaction(hearth, db)
            hearth.save_resident_in_transaction(
                db,
                "reader",
                Declaration(
                    "Reader",
                    "A daily summary of synthetic notes. Read-only; no external actions.",
                    10_000_000,
                    budget_timezone="Europe/Ljubljana",
                ),
                expected_revision=0,
            )
            save_selection(
                db,
                "reader",
                [{"input_set_id": input_set_id}],
                expected_revision=0,
                command_id="seed-reader-inputs",
                actor="operator",
                now=int(hearth.clock()),
            )
    return hearth.resident("reader")


def seed_reader_via(client):
    """Seed the Reader example for a TestClient, replacing the old demo endpoint."""
    return seed_reader(client.app.state.hearth)
