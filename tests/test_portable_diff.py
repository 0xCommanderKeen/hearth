"""Rehearsal comparisons show exact state changes without executing either archive."""

import hashlib
import json
import subprocess
import sys

import pytest
from hearth.backup import capture
from hearth.core import Hearth
from hearth.database import Database
from hearth.models import Declaration, Refused
from hearth.portable import compare, export, import_state


@pytest.fixture
def archive(tmp_path):
    db = Database(tmp_path / "source/hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic purpose", 10000), expected_revision=0
    )
    hearth.set_paused("reader", paused=True, expected_revision=0)
    capture(tmp_path / "source", tmp_path / "backup")
    export(tmp_path / "backup", tmp_path / "export")
    return (tmp_path / "export/state.json").read_bytes()


def test_reordered_rows_and_epoch_are_not_operational_changes(archive):
    changed = json.loads(archive)
    changed["source_epoch"] = "new-observation-epoch"
    for rows in changed["tables"].values():
        rows.reverse()
    result = compare(archive, json.dumps(changed).encode())
    assert result["equal"] and result["change_count"] == 0 and result["changes"] == []
    assert result["before_sha256"] == result["after_sha256"]
    assert result["source_epochs"]["before"] != result["source_epochs"]["after"]
    assert result["source_epochs"]["affects_equality"] is False


def test_composite_identity_and_exact_field_changes_are_reported(archive):
    changed = json.loads(archive)
    changed["tables"]["declarations"][0]["daily_limit"] = 9000
    changed["tables"]["declarations"][0]["purpose"] = "Changed synthetic purpose"
    changed["tables"]["operator_controls"][0]["paused"] = 0
    result = compare(archive, json.dumps(changed).encode())
    assert not result["equal"]
    assert result["totals"] == {"added": 0, "removed": 0, "modified": 2}
    declaration = result["changes"][0]
    assert declaration["identity"] == {"resident_id": "reader", "revision": 1}
    assert declaration["fields"] == {
        "daily_limit": {"before": 10000, "after": 9000},
        "purpose": {"before": "Synthetic purpose", "after": "Changed synthetic purpose"},
    }
    assert result["changes"][1]["fields"] == {"paused": {"before": 1, "after": 0}}


def test_added_removed_rows_and_files_are_distinguished(archive):
    changed = json.loads(archive)
    changed["tables"]["operator_controls"] = []
    changed["tables"]["declarations"].append(changed["tables"]["declarations"][0] | {"revision": 2})
    changed["files"]["artifacts/orphan.md"] = {
        "text": "Synthetic orphan",
        "sha256": hashlib.sha256(b"Synthetic orphan").hexdigest(),
    }
    raw = json.dumps(changed).encode()
    result = compare(archive, raw)
    assert result["totals"] == {"added": 2, "removed": 1, "modified": 0}
    file = result["changes"][-1]
    assert file["identity"] == {"path": "artifacts/orphan.md"}
    assert file["fields"]["bytes"] == {"before": None, "after": 16}
    assert "text" not in file["fields"]
    reverse = compare(raw, archive)
    assert reverse["totals"] == {"added": 1, "removed": 2, "modified": 0}


def test_file_changes_use_hash_and_size_without_dumping_content(archive):
    left = json.loads(archive)
    right = json.loads(archive)
    for doc, content in ((left, "Synthetic first"), (right, "Synthetic second")):
        doc["files"]["mock-runtime/orphan.json"] = {
            "text": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    result = compare(json.dumps(left).encode(), json.dumps(right).encode())
    assert result["totals"]["modified"] == 1
    assert set(result["changes"][0]["fields"]) == {"sha256", "bytes"}
    assert "Synthetic" not in json.dumps(result)


@pytest.mark.parametrize("limit", [0, 1])
def test_limited_output_keeps_complete_counts_and_never_claims_equality(archive, limit):
    changed = json.loads(archive)
    changed["tables"]["declarations"][0]["daily_limit"] = 9000
    changed["tables"]["operator_controls"][0]["paused"] = 0
    result = compare(archive, json.dumps(changed).encode(), limit=limit)
    assert not result["equal"] and result["change_count"] == 2
    assert len(result["changes"]) == limit and result["omitted_changes"] == 2 - limit


@pytest.mark.parametrize("limit", [-1, True, 10001, 1.5])
def test_invalid_limits_are_refused(archive, limit):
    with pytest.raises(Refused, match="portable_diff_limit_invalid"):
        compare(archive, archive, limit=limit)


@pytest.mark.parametrize("side", ["left", "right"])
def test_invalid_input_is_refused_even_with_zero_output_limit(archive, side):
    with pytest.raises(Refused):
        compare(
            b"{}" if side == "left" else archive, b"{}" if side == "right" else archive, limit=0
        )


def test_import_reverse_export_has_no_semantic_changes(archive, tmp_path):
    import_state(archive, tmp_path / "imported")
    capture(tmp_path / "imported", tmp_path / "reverse-backup")
    export(tmp_path / "reverse-backup", tmp_path / "reverse")
    assert compare(archive, (tmp_path / "reverse/state.json").read_bytes())["equal"] is True


def test_cli_distinguishes_equal_different_and_invalid_inputs(archive, tmp_path):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_bytes(archive)
    after.write_bytes(archive)
    command = [
        sys.executable,
        "-m",
        "hearth",
        "diff-state",
        "--source",
        str(before),
        "--against",
        str(after),
    ]
    equal = subprocess.run(command, capture_output=True, text=True)
    assert equal.returncode == 0 and json.loads(equal.stdout)["equal"]
    changed = json.loads(archive)
    changed["tables"]["declarations"][0]["daily_limit"] = 9000
    after.write_text(json.dumps(changed))
    different = subprocess.run(command, capture_output=True, text=True)
    assert different.returncode == 1 and not json.loads(different.stdout)["equal"]
    after.write_text("{}")
    invalid = subprocess.run(command, capture_output=True, text=True)
    assert invalid.returncode == 2 and not invalid.stdout
