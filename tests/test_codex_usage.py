import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace

import pytest
from hearth.codex_events import TokenUsage
from hearth.codex_usage import UsageBinding, UsageJournal

BINDING = UsageBinding("run_1", "a" * 64, "gpt-6-astra", "standard")
USAGE = TokenUsage(30, 10, 8, 3, 5)


def transcript(usage=USAGE):
    return "\n".join(
        json.dumps(event)
        for event in [
            {"type": "thread.started", "thread_id": "thread_1"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "message", "type": "agent_message", "text": "summary"},
            },
            {"type": "turn.completed", "usage": asdict(usage)},
        ]
    )


def test_reopen_preserves_request_usage_and_rounds_once(tmp_path):
    root = tmp_path / "journal"
    journal = UsageJournal.create(root, BINDING)
    journal.complete(journal.begin(), USAGE)
    reopened = UsageJournal(root, BINDING)
    reopened.complete(reopened.begin(), USAGE)
    combined = TokenUsage(**{field: value * 2 for field, value in asdict(USAGE).items()})
    result = reopened.seal(transcript(combined), exit_code=0, final="summary")
    assert result.microdollars == 1245
    assert UsageJournal(root, BINDING).estimate() == result
    assert reopened.seal(transcript(combined), exit_code=0, final="summary") == result
    with pytest.raises(ValueError, match="sealed"):
        reopened.begin()


def test_intent_without_terminal_usage_never_allows_another_dispatch_or_seal(tmp_path):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)
    request = journal.begin()
    reopened = UsageJournal(journal.root, BINDING)
    with pytest.raises(FileNotFoundError):
        reopened.begin()
    with pytest.raises(FileNotFoundError):
        reopened.seal(transcript(), exit_code=0, final="summary")
    assert not (journal.root / "terminal.json").exists()
    reopened.complete(request, USAGE)
    assert reopened.seal(transcript(), exit_code=0, final="summary").microdollars == 623


def test_duplicate_completion_does_not_double_charge_but_conflict_is_sticky(tmp_path):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)
    request = journal.begin()
    journal.complete(request, USAGE)
    journal.complete(request, USAGE)
    with pytest.raises(ValueError, match="conflicting"):
        journal.complete(request, replace(USAGE, input_tokens=31))
    with pytest.raises(ValueError, match="conflicting"):
        UsageJournal(journal.root, BINDING).seal(transcript(), exit_code=0, final="summary")


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "other"},
        {"input_digest": "b" * 64},
        {"mode": "fast"},
        {"schedule": "new"},
        {"model": "other"},
    ],
)
def test_changed_binding_refuses_without_rewriting(tmp_path, change):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)
    original = (journal.root / "binding.json").read_bytes()
    with pytest.raises(ValueError):
        UsageJournal(journal.root, replace(BINDING, **change))
    assert (journal.root / "binding.json").read_bytes() == original


@pytest.mark.parametrize(
    "stdout,exit_code,final",
    [
        (transcript(replace(USAGE, input_tokens=31)), 0, "summary"),
        (transcript(), 0, "changed"),
        (transcript(), 0, None),
        (transcript(), -9, "summary"),
        ("truncated", 0, "summary"),
    ],
)
def test_bad_terminal_evidence_cannot_be_replaced_with_better_looking_output(
    tmp_path, stdout, exit_code, final
):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)
    journal.complete(journal.begin(), USAGE)
    with pytest.raises(ValueError):
        journal.seal(stdout, exit_code=exit_code, final=final)
    with pytest.raises(ValueError):
        journal.seal(transcript(), exit_code=0, final="summary")


def test_missing_counts_seal_as_unknown_and_prevent_further_dispatch(tmp_path):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)
    journal.complete(journal.begin(), replace(USAGE, cache_write_input_tokens=None))
    with pytest.raises(ValueError, match="unknown"):
        journal.begin()
    result = journal.seal(transcript(), exit_code=0, final="summary")
    assert result.microdollars is None and result.reason == "missing_usage"


@pytest.mark.parametrize("damage", ["partial", "symlink", "hardlink", "orphan"])
def test_unsafe_or_corrupt_files_do_not_produce_estimates(tmp_path, damage):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)
    journal.complete(journal.begin(), USAGE)
    path = journal.root / "usage-000.json"
    if damage == "partial":
        path.write_text('{"input_tokens":')
    elif damage == "symlink":
        path.rename(tmp_path / "outside")
        path.symlink_to(tmp_path / "outside")
    elif damage == "hardlink":
        os.link(path, tmp_path / "outside")
    else:
        (journal.root / "usage-099.json").write_text("{}")
    with pytest.raises((ValueError, OSError)):
        journal.seal(transcript(), exit_code=0, final="summary")


def test_lost_sync_acknowledgement_leaves_intent_unresolved(tmp_path, monkeypatch):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)

    def failed_sync(fd):
        raise OSError("sync failed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", failed_sync)
        with pytest.raises(OSError, match="sync failed"):
            journal.begin()
    assert (journal.root / "request-000.json").exists()
    with pytest.raises(FileNotFoundError):
        UsageJournal(journal.root, BINDING).begin()


def test_competing_dispatch_intents_cannot_bypass_unresolved_usage(tmp_path):
    journal = UsageJournal.create(tmp_path / "journal", BINDING)

    def begin():
        try:
            return UsageJournal(journal.root, BINDING).begin()
        except FileNotFoundError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(begin) for _ in range(2)]
    assert sorted((future.result() for future in futures), key=lambda value: value is None) == [
        0,
        None,
    ]
    assert len(list(journal.root.glob("request-*.json"))) == 1
