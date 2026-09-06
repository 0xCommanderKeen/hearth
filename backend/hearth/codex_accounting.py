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
    if receipt.get("kind") == "codex_subscription":
        from hearth.codex_live import encode

        return encode(receipt, expected)
    try:
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode()) > MAX_STREAM:
            raise ValueError("oversized receipt")
        value = json.loads(raw, object_pairs_hook=unique_object)
        operational = "containers" in value
        keys = {"binding", "requests", "terminal"}
        if operational:
            keys |= {"assets", "containers", "stopped", "journal"}
        if set(value) != keys or value["binding"] != asdict(expected):
            raise ValueError("receipt binding mismatch")
        if not isinstance(value["requests"], list) or len(value["requests"]) > MAX_REQUESTS:
            raise ValueError("invalid request sequence")
        if operational:
            stopped = verify_runtime_receipt(value, expected)
            if stopped is not None:
                return raw, hashlib.sha256(raw.encode()).hexdigest(), stopped
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
    if row is None or run["finished_at"] is None:
        raise Refused("backup_priced_run_unsettled")
    try:
        raw, digest, evidence = encode_receipt(json.loads(row["receipt"]), binding(db, run))
    except ValueError, TypeError:
        raise Refused("backup_runtime_invalid") from None
    if raw != row["receipt"] or digest != row["sha256"] or evidence.status != run["status"]:
        raise Refused("backup_runtime_invalid")
    value = json.loads(raw)
    if run["runtime_kind"] == "codex_subscription":
        pin = db.execute("SELECT value FROM system_meta WHERE key='codex_live_binary'").fetchone()
        if (
            pin is None
            or value.get("binary") != pin[0]
            or value.get("kind") != "codex_subscription"
        ):
            raise Refused("backup_runtime_invalid")
    if run["runtime_kind"] == "codex_mock":
        assets = db.execute("SELECT value FROM system_meta WHERE key='codex_assets'").fetchone()
        if assets is None or value.get("assets") != assets[0] or "containers" not in value:
            raise Refused("backup_runtime_invalid")
    if (
        not run["launch_attempted"]
        and not (
            run["runtime_kind"] == "codex_subscription"
            and run["cancellation_requested"]
            and value.get("launched") is False
            and value.get("cancelled") is True
        )
        and not (
            run["runtime_kind"] == "codex_mock"
            and run["cancellation_requested"]
            and evidence.status == "cancelled"
            and evidence.cost == 0
            and value.get("containers") == {"cli": None, "collector": None}
        )
    ):
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


def verify_runtime_receipt(value, expected):
    from hearth.codex_container import CodexContainer

    containers = value["containers"]
    if (
        set(containers) != {"cli", "collector"}
        or not isinstance(value["assets"], str)
        or len(value["assets"]) != 64
    ):
        raise Refused("run_usage_invalid")
    for role, item in containers.items():
        if item is not None:
            CodexContainer.validate_export(item)
            if item["claim"]["binding"] != asdict(expected) or item["claim"]["role"] != role:
                raise Refused("run_usage_invalid")
    cli, collector = containers["cli"], containers["collector"]
    if cli is not None and (
        collector is None or cli["claim"]["network"] != "container:" + collector["identity"]["id"]
    ):
        raise Refused("run_usage_invalid")
    if value["stopped"] is not None:
        if value["stopped"] not in {"cancelled", "failed"} or value["terminal"] is not None:
            raise Refused("run_usage_invalid")
        return Evidence(value["stopped"], cost=0 if cli is None or cli["start"] is None else None)
    if (
        cli is None
        or collector is None
        or any(
            item["terminal"]["status"] != "exited" or item["terminal"]["exit_code"] != 0
            for item in (cli, collector)
        )
    ):
        raise Refused("run_usage_invalid")
    output = json.loads(cli["terminal"]["logs"])
    if value["terminal"] != {
        "stdout": output["stdout"],
        "exit_code": output["returncode"],
        "final": output["final"],
    }:
        raise Refused("run_usage_invalid")
    return None
