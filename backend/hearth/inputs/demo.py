"""Explicit idempotent Reader setup, shared by the browser and CLI example."""

from hearth.inputs.catalog import Inputs
from hearth.inputs.selection import save_selection
from hearth.residents.models import Declaration
from hearth.work.service import Hearth


def seed_reader(hearth: Hearth):
    with hearth.database.transaction(write=True) as db:
        if not db.execute("SELECT 1 FROM residents WHERE id='reader'").fetchone():
            input_set_id = Inputs(hearth).seed_in_transaction(db)
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
