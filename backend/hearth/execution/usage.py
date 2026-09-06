"""Admission pins and immutable SQLite receipts for provider usage accounting."""

import hashlib
import json

from hearth.integrations.interface import (
    encode_receipt,
    receipt_requests,
    usage_binding,
    validate_pricing,
    validate_receipt_pins,
)
from hearth.residents.models import Refused


def pricing(db, run_id: str) -> dict | None:
    row = db.execute(
        "SELECT model,mode,schedule FROM run_pricing WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    value = dict(row)
    validate_pricing(value)
    return value | {"basis": "api_equivalent_estimate"}


def details(db, run_id: str) -> dict | None:
    value = pricing(db, run_id)
    if value is None:
        return None
    receipt = db.execute(
        "SELECT receipt,sha256 FROM run_usage WHERE run_id=?", (run_id,)
    ).fetchone()
    if receipt is not None:
        value |= {
            "receipt_sha256": receipt["sha256"],
            "requests": receipt_requests(receipt["receipt"]),
        }
    return value


def binding(db, run):
    pin = pricing(db, run["id"])
    if pin is None:
        raise Refused("run_pricing_required")
    return usage_binding(run["id"], run["input_digest"], pin)


def verify_stored(db, run) -> None:
    """Backup verification uses SQLite evidence, never the former worker's files."""
    row = db.execute("SELECT receipt,sha256 FROM run_usage WHERE run_id=?", (run["id"],)).fetchone()
    if row is None or run["finished_at"] is None:
        raise Refused("backup_priced_run_unsettled")
    try:
        raw, digest, evidence = encode_receipt(json.loads(row["receipt"]), binding(db, run))
    except ValueError, TypeError:
        raise Refused("backup_runtime_invalid") from None
    if raw != row["receipt"] or digest != row["sha256"] or evidence.status != run["status"]:
        raise Refused("backup_runtime_invalid")
    try:
        before_launch = validate_receipt_pins(
            run["runtime_kind"],
            json.loads(raw),
            runtime_pins(db, run["id"]),
            cancelled=bool(run["cancellation_requested"]),
        )
    except Refused:
        raise Refused("backup_runtime_invalid") from None
    if not run["launch_attempted"] and not before_launch:
        raise Refused("backup_runtime_invalid")
    reconciliation = db.execute(
        "SELECT amount FROM usage_reconciliations WHERE run_id=?", (run["id"],)
    ).fetchone()
    cost = evidence.cost
    if reconciliation is not None:
        if cost is not None:
            raise Refused("backup_runtime_invalid")
        cost = reconciliation["amount"]
    if run["actual_cost"] != cost or bool(run["usage_known"]) != (cost is not None):
        raise Refused("backup_runtime_invalid")
    if evidence.output is not None:
        artifact = db.execute(
            "SELECT sha256 FROM artifacts WHERE id=? AND run_id=?", (run["artifact_id"], run["id"])
        ).fetchone()
        if (
            artifact is None
            or artifact["sha256"] != hashlib.sha256(evidence.output.encode()).hexdigest()
        ):
            raise Refused("backup_runtime_invalid")

    elif run["artifact_id"] is not None:
        raise Refused("backup_runtime_invalid")


def runtime_pins(db, run_id: str | None = None) -> dict:
    result = dict(db.execute("SELECT key,value FROM system_meta"))
    if run_id is not None:
        row = db.execute("SELECT * FROM run_management WHERE run_id=?", (run_id,)).fetchone()
        result["management"] = dict(row) if row is not None else None
    return result
