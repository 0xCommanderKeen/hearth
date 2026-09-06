"""Resolve missing mock accounting with an explicit immutable operator report."""

import hashlib
import json

from hearth.core import Hearth, _audit
from hearth.models import Refused, bounded_text, identifier, microdollars


class Accounting:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def reconcile(self, command_id: str, run_id: str, *, amount: int, evidence: str) -> dict:
        identifier(command_id)
        identifier(run_id)
        microdollars(amount)
        bounded_text(evidence, 2000, "usage_evidence_required")
        digest = hashlib.sha256(
            json.dumps([run_id, amount, evidence], separators=(",", ":")).encode()
        ).hexdigest()
        with self.hearth.database.transaction(write=True) as db:
            previous = db.execute(
                "SELECT * FROM usage_reconciliations WHERE command_id=?", (command_id,)
            ).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise Refused("reconciliation_conflict")
                return dict(previous)
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise Refused("run_not_found")
            if run["finished_at"] is None or run["status"] not in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                raise Refused("terminal_run_required")
            if run["usage_known"]:
                raise Refused("usage_already_recorded")
            now = int(self.hearth.clock())
            db.execute(
                "INSERT INTO usage_reconciliations VALUES "
                "(?, ?, ?, ?, ?, ?, 'operator_reported_mock')",
                (run_id, command_id, digest, amount, evidence, now),
            )
            db.execute("UPDATE runs SET actual_cost=?, usage_known=1 WHERE id=?", (amount, run_id))
            # This record accounts for one run. It cannot release another run's
            # hold or an independent operator control.
            other = db.execute(
                "SELECT id FROM runs WHERE resident_id=? AND usage_known=0 "
                "AND finished_at IS NOT NULL ORDER BY finished_at,id LIMIT 1",
                (run["resident_id"],),
            ).fetchone()
            if other:
                changed = db.execute(
                    "UPDATE pauses SET run_id=? WHERE resident_id=? "
                    "AND run_id=? AND reason='usage_unknown'",
                    (other[0], run["resident_id"], run_id),
                ).rowcount
                if changed:
                    _audit(
                        db,
                        "resident.usage_hold_reassigned",
                        run["resident_id"],
                        now,
                        {"run_id": other[0]},
                    )
            else:
                changed = db.execute(
                    "DELETE FROM pauses WHERE resident_id=? AND run_id=? "
                    "AND reason='usage_unknown'",
                    (run["resident_id"], run_id),
                ).rowcount
                if changed:
                    _audit(
                        db,
                        "resident.usage_hold_resolved",
                        run["resident_id"],
                        now,
                        {"run_id": run_id},
                    )
            _audit(
                db,
                "run.usage_reconciled",
                run_id,
                now,
                {
                    "amount": amount,
                    "source": "operator_reported_mock",
                    "command_id": command_id,
                    "budget_day": run["budget_day"],
                },
            )
            return dict(
                db.execute(
                    "SELECT * FROM usage_reconciliations WHERE run_id=?", (run_id,)
                ).fetchone()
            )
