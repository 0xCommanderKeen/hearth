"""Village places follow actual private-tool receipts in temporary SQLite."""

import json
from concurrent.futures import ThreadPoolExecutor

from hearth.observation.snapshot import snapshot
from hearth.residents.models import Declaration

from tests.management.test_memory_tools import manager, settle, working_run


def test_latest_receipt_is_body_free_replay_stable_and_survives_snapshot_window(tmp_path):
    app, hearth, resident = manager(tmp_path)
    run, call = working_run(app, resident, "village")
    assert call("read", "hearth_memory_read", {})[0]
    action = snapshot(hearth)["runs"][0]["action"]
    assert action["place"] == "research"
    assert set(action) == {"place", "label", "at", "sequence"}
    assert call("read", "hearth_memory_read", {})[0]
    assert snapshot(hearth)["runs"][0]["action"] == action
    assert call("write", "hearth_journal_write", {"text": "private synthetic canary"})[0]
    for i in range(35):
        hearth.save_resident(
            "extra", Declaration(f"Extra {i}", "Synthetic", 100000), expected_revision=i
        )
    state = snapshot(hearth)
    latest = next(r for r in state["runs"] if r["id"] == run.id)["action"]
    assert latest["place"] == "workshop" and latest["sequence"] > action["sequence"]
    assert "private synthetic canary" not in json.dumps(latest)
    assert not any(a["kind"] == "management.tool_completed" for a in state["activity"])
    settle(app, run)
    second, _ = working_run(app, resident, "next")
    assert next(r for r in snapshot(hearth)["runs"] if r["id"] == second.id)["action"] is None


def test_refused_action_does_not_claim_a_successful_visit(tmp_path):
    app, hearth, resident = manager(tmp_path)
    _, call = working_run(app, resident, "refusal")
    assert call("read", "hearth_memory_read", {})[0]
    assert not call("missing", "hearth_skills_read", {"skill_id": "absent", "revision": 1})[0]
    assert snapshot(hearth)["runs"][0]["action"] is None


def test_concurrent_replay_has_one_receipt_and_no_snapshot_side_effects(tmp_path):
    app, hearth, resident = manager(tmp_path)
    _, call = working_run(app, resident, "concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: call("read", "hearth_memory_read", {}), range(2)))
    assert all(answer[0] for answer in answers)
    before = snapshot(hearth)
    after = snapshot(hearth)
    assert before["cursor"] == after["cursor"]
    assert before["runs"][0]["action"] == after["runs"][0]["action"]
    events = [a for a in after["activity"] if a["kind"] == "management.tool_completed"]
    assert len(events) == 1
