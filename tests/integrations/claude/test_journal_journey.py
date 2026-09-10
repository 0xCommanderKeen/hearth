"""The memory and journal journey on Claude: one run writes, the next one reads it back.

The same shape as the Codex journey in `tests/integrations/codex/test_journal_journey.py`,
over the bridge this slice adds. The scripted CLI has no database of its own: whatever
the second run repeats about the first day, it read through `hearth_memory_read` over
the socket, from the note the first run wrote with `hearth_memory_save`.

Neither run holds a management grant. Writing one's own memory and journal is a
declared capability, not a management power (`docs/adr/0012`), and this is that rule
carried onto a second provider.
"""

import json

from hearth.residents.journal import Journal
from hearth.residents.memory import Memory
from hearth.storage.backup import capture, restore, verify

from tests.integrations.claude.test_mcp_bridge import Store, answer

NOTE = "The orchard is fictional. Day one brought 12 pears."
ENTRY = "Day 1: reported 12 pears and wrote the note."


def test_the_second_run_opens_with_what_the_first_run_wrote_over_the_bridge(tmp_path):
    from hearth.execution.context import read_context

    store = Store(tmp_path)
    store.script(
        [
            {
                "tool": "hearth_memory_save",
                "arguments": {
                    "resident_id": "writer",
                    "text": NOTE,
                    "expected_revision": 0,
                    "operation_id": "day-one-note",
                },
            },
            {"tool": "hearth_journal_write", "arguments": {"text": ENTRY}},
        ]
    )
    first = store.run("day-one")
    store.work(first)
    assert store.settle(first).status == "succeeded"

    saved, written = (answer(call["reply"]) for call in store.record()["calls"])
    assert saved["revision"] == 1 and saved["author"] == "run"
    assert written["sequence"] == 1 and written["run_id"] == first.id
    assert Memory(store.hearth).read("writer")["text"] == NOTE
    entries = Journal(store.hearth).read("writer")["entries"]
    assert [entry["text"] for entry in entries] == [ENTRY]

    # The second day opens with the first day's note and entry, whatever it does next.
    store.script([{"tool": "hearth_memory_read", "arguments": {}}], answer_prefix="My note says: ")
    second = store.run("day-two")
    with store.hearth.database.transaction() as db:
        opening = read_context(db, second.id, Memory(store.hearth).files)
    assert opening["memory"]["revision"] == 1 and opening["memory"]["text"] == NOTE
    assert [item["sequence"] for item in opening["journal"]] == [1]

    store.work(second)
    result = store.settle(second)
    assert result.status == "succeeded"
    read_back = answer(store.record()["calls"][0]["reply"])
    assert read_back["text"] == NOTE and read_back["revision"] == 1
    # And the note reached the session's own answer, which is what the operator reads.
    report = store.execution.artifact(result.artifact_id)[1]
    assert report.startswith("My note says: ") and NOTE in report

    # Neither run held management authority, and the operator's view says so.
    from hearth.observation.snapshot import snapshot

    runs = {row["id"]: row for row in snapshot(store.hearth)["runs"]}
    assert runs[first.id]["management"] is None
    assert runs[first.id]["memory_written"] == [1]
    assert runs[first.id]["journal_written"] == 1
    assert runs[second.id]["journal_opened"] == [1]

    # The whole journey survives a backup, receipts and management pins included.
    capture(tmp_path / "data", tmp_path / "backup")
    manifest = verify(tmp_path / "backup")
    assert not any("claude-live" in name for name in manifest["files"])
    restore(tmp_path / "backup", tmp_path / "held")
    from hearth.storage.database import Database
    from hearth.work.service import Hearth

    held = Hearth(Database(tmp_path / "held/hearth.db"))
    assert held.database.restored()
    assert Journal(held).read("writer")["entries"] == entries
    assert Memory(held).read("writer")["text"] == NOTE
    # The stream that commits is still the CLI's own, tool calls and all.
    stored = store.rows("SELECT receipt FROM run_usage WHERE run_id=?", first.id)[0]["receipt"]
    assert json.loads(stored)["management"]["error"] is None
