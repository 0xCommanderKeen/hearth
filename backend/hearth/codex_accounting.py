"""Admission pins and immutable SQLite receipts for synthetic Codex accounting."""

import hashlib
import json
from dataclasses import asdict

from hearth.codex_events import MAX_STREAM, unique_object
from hearth.codex_pricing import MAX_REQUESTS, MODEL, PRICE_SCHEDULE
from hearth.codex_usage import UsageBinding, interpret_details
from hearth.models import Refused
from hearth.runtime import Evidence


def pricing(db, run_id: str) -> dict | None:
    row = db.execute(
        "SELECT model,mode,schedule FROM run_pricing WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    value = dict(row)
    if (
        value["model"] != MODEL
        or value["mode"] not in {"standard", "fast"}
        or value["schedule"] != PRICE_SCHEDULE
    ):
        raise Refused("run_pricing_invalid")
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
            "requests": json.loads(receipt["receipt"])["requests"],
        }
    return value


def binding(db, run) -> UsageBinding:
    pin = pricing(db, run["id"])
    if pin is None:
        raise Refused("run_pricing_required")
    return UsageBinding(run["id"], run["input_digest"], pin["model"], pin["mode"], pin["schedule"])


def encode_receipt(receipt: dict, expected: UsageBinding) -> tuple[str, str, Evidence]:
    """Validate the exact serialized copy that will commit with accounting/audit."""
    try:
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode()) > MAX_STREAM:
            raise ValueError("oversized receipt")
        value = json.loads(raw, object_pairs_hook=unique_object)
        if set(value) != {"binding", "requests", "terminal"} or value["binding"] != asdict(
            expected
        ):
            raise ValueError("receipt binding mismatch")
        if not isinstance(value["requests"], list) or len(value["requests"]) > MAX_REQUESTS:
            raise ValueError("invalid request sequence")
        transcript, estimate = interpret_details(value["requests"], value["terminal"], expected)
        if estimate.reason not in {None, "missing_usage", "missing_or_excessive_requests"}:
            raise ValueError("invalid usage")
        output = (
            "Simulation — no model was called.\n\n" + transcript.output
            if transcript.output is not None
            else None
        )
        return (
            raw,
            hashlib.sha256(raw.encode()).hexdigest(),
            Evidence(
                "succeeded" if transcript.status == "completed" else "failed",
                output,
                estimate.microdollars,
            ),
        )
    except ValueError, TypeError, KeyError, AttributeError, RecursionError:
        raise Refused("run_usage_invalid") from None


def verify_stored(db, run) -> None:
    """Backup verification uses SQLite evidence, never the former worker's files."""
    row = db.execute("SELECT receipt,sha256 FROM run_usage WHERE run_id=?", (run["id"],)).fetchone()
    if row is None or run["finished_at"] is None or not run["launch_attempted"]:
        raise Refused("backup_priced_run_unsettled")
    try:
        raw, digest, evidence = encode_receipt(json.loads(row["receipt"]), binding(db, run))
    except ValueError, TypeError:
        raise Refused("backup_runtime_invalid") from None
    if raw != row["receipt"] or digest != row["sha256"] or evidence.status != run["status"]:
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
