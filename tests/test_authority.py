"""Real SQLite decisions, concurrent reviews, and atomic audit failure."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.artifacts import Artifacts
from hearth.authority import Authority
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.runtime import MockRuntime


@pytest.fixture
def system(tmp_path):
    now = [1_788_640_000]
    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: now[0])
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000), expected_revision=0
    )
    artifacts = Artifacts(tmp_path / "artifacts")
    task = hearth.submit("summary", "reader", "Synthetic summary", expires_at=now[0] + 600)
    run = hearth.admit(task.task_id, reserve=5_000)
    Executor(Execution(hearth, artifacts), MockRuntime(tmp_path / "runtime")).step()
    authority = Authority(hearth, artifacts)
    return authority, run.id, now


def propose(system):
    authority, artifact, now = system
    authority.set_publication_policy("reader", enabled=True, expected_revision=0)
    return authority.request("review", artifact, expires_at=now[0] + 300)


def test_default_denial_and_exact_review_binding(system):
    authority, artifact, now = system
    with pytest.raises(Refused, match="publication_not_granted"):
        authority.request("review", artifact, expires_at=now[0] + 300)
    proposal = propose(system)
    assert proposal.payload["simulated"] is True
    assert proposal.payload["destination"] == "mock-noticeboard"
    assert proposal.payload["resident_revision"] == 1
    assert proposal.payload["policy_revision"] == 1
    assert len(proposal.payload["sha256"]) == 64
    with pytest.raises(Refused, match="approval_digest_mismatch"):
        authority.decide(proposal.id, reviewed_digest="changed", approve=True)
    assert authority.inspect(proposal.id).status == "pending"


def test_first_decision_wins_under_concurrent_opposite_reviews(system):
    authority, _, _ = system
    proposal = propose(system)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda approve: authority.decide(
                    proposal.id, reviewed_digest=proposal.digest, approve=approve
                ),
                [True, False],
            )
        )
    assert outcomes[0] == outcomes[1]
    decisions = [
        fact
        for fact in authority.hearth.audit()
        if fact["kind"] in ("approval.approved", "approval.denied")
    ]
    assert len(decisions) == 1


def test_deadline_is_exclusive_and_expired_request_cannot_be_revived(system):
    authority, _, now = system
    proposal = propose(system)
    now[0] = proposal.expires_at
    assert (
        authority.decide(proposal.id, reviewed_digest=proposal.digest, approve=True).status
        == "expired"
    )
    now[0] -= 1
    assert (
        authority.decide(proposal.id, reviewed_digest=proposal.digest, approve=True).status
        == "expired"
    )


def test_lost_request_ack_returns_original_even_after_revocation_and_expiry(system):
    authority, artifact, now = system
    proposal = propose(system)
    authority.set_publication_policy("reader", enabled=False, expected_revision=1)
    now[0] += 1000
    assert authority.request("review", artifact, expires_at=proposal.expires_at) == proposal
    with pytest.raises(Refused, match="approval_conflict"):
        authority.request("review", artifact, expires_at=proposal.expires_at + 1)


def test_decision_audit_failure_rolls_back_decision(system):
    authority, _, _ = system
    proposal = propose(system)
    with authority.hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER audit_failure BEFORE INSERT ON audit
            WHEN NEW.kind = 'approval.approved'
            BEGIN SELECT RAISE(ABORT, 'injected disk failure'); END""")
    with pytest.raises(Exception, match="injected disk failure"):
        authority.decide(proposal.id, reviewed_digest=proposal.digest, approve=True)
    assert authority.inspect(proposal.id).status == "pending"


def test_policy_revision_cannot_lose_a_concurrent_revocation(system):
    authority, _, _ = system
    propose(system)
    authority.set_publication_policy("reader", enabled=False, expected_revision=1)
    with pytest.raises(Refused, match="revision_conflict"):
        authority.set_publication_policy("reader", enabled=True, expected_revision=1)


def test_corrupt_artifact_cannot_be_proposed(system):
    authority, artifact, now = system
    authority.set_publication_policy("reader", enabled=True, expected_revision=0)
    (authority.artifacts.root / (artifact + ".md")).write_text("modified")
    with pytest.raises(Refused, match="artifact_corrupt"):
        authority.request("review", artifact, expires_at=now[0] + 300)
    with pytest.raises(Refused, match="approval_not_found"):
        authority.inspect("review")


def approved_broker(system, tmp_path):
    from hearth.broker import Broker, MockNoticeboard

    authority, _, _ = system
    proposal = propose(system)
    authority.decide(proposal.id, reviewed_digest=proposal.digest, approve=True)
    return Broker(authority, MockNoticeboard(tmp_path / "noticeboard")), proposal


def test_effect_is_once_and_acknowledgement_replay_does_not_change_target(system, tmp_path):
    broker, proposal = approved_broker(system, tmp_path)
    result = broker.execute(proposal.id)
    assert result["status"] == "completed"
    assert broker.execute(proposal.id) == result
    assert len(list((tmp_path / "noticeboard").glob("*.md"))) == 1
    with broker.authority.hearth.database.transaction() as db:
        assert db.execute("SELECT revision FROM publication_targets").fetchone()[0] == 2


def test_revocation_after_review_prevents_effect(system, tmp_path):
    broker, proposal = approved_broker(system, tmp_path)
    broker.authority.set_publication_policy("reader", enabled=False, expected_revision=1)
    assert broker.execute(proposal.id)["reason"] == "grant_changed"
    assert not list((tmp_path / "noticeboard").glob("*.md"))


def test_lost_effect_ack_recovers_after_revocation_without_another_publish(
    system, tmp_path, monkeypatch
):
    broker, proposal = approved_broker(system, tmp_path)
    original = broker.effect.publish
    calls = []

    def lost_ack(*args):
        calls.append(args[0])
        original(*args)
        raise OSError("ack lost")

    monkeypatch.setattr(broker.effect, "publish", lost_ack)
    assert broker.execute(proposal.id)["status"] == "unknown"
    broker.authority.set_publication_policy("reader", enabled=False, expected_revision=1)
    assert broker.execute(proposal.id)["status"] == "completed"
    assert calls == [proposal.id]


def test_ambiguous_non_idempotent_effect_is_never_resent(system, tmp_path, monkeypatch):
    broker, proposal = approved_broker(system, tmp_path)
    calls = []

    def ambiguous(*args):
        calls.append(args[0])
        raise OSError("provider might have performed the action")

    monkeypatch.setattr(broker.effect, "publish", ambiguous)
    assert broker.execute(proposal.id)["status"] == "unknown"
    assert broker.execute(proposal.id)["status"] == "unknown"
    assert calls == [proposal.id]
    authority, artifact, now = system
    second = authority.request("second", artifact, expires_at=now[0] + 300)
    authority.decide(second.id, reviewed_digest=second.digest, approve=True)
    with pytest.raises(Refused, match="destination_busy"):
        broker.execute(second.id)


def test_completion_audit_crash_recovers_from_receipt(system, tmp_path):
    broker, proposal = approved_broker(system, tmp_path)
    with broker.authority.hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER audit_failure BEFORE INSERT ON audit
            WHEN NEW.kind = 'action.completed'
            BEGIN SELECT RAISE(ABORT, 'completion disk failure'); END""")
    with pytest.raises(Exception, match="completion disk failure"):
        broker.execute(proposal.id)
    assert broker.inspect(proposal.id)["status"] == "executing"
    with broker.authority.hearth.database.transaction(write=True) as db:
        assert db.execute("SELECT revision FROM publication_targets").fetchone()[0] == 1
        db.execute("DROP TRIGGER audit_failure")
    assert broker.execute(proposal.id)["status"] == "completed"
    assert len(list((tmp_path / "noticeboard").glob("*.md"))) == 1


@pytest.mark.parametrize(
    "change,reason",
    [
        ("expiry", "approval_expired"),
        ("resident", "resident_changed"),
        ("artifact", "artifact_corrupt"),
    ],
)
def test_changed_authority_or_payload_refuses_dispatch(system, tmp_path, change, reason):
    broker, proposal = approved_broker(system, tmp_path)
    authority, artifact, now = system
    if change == "expiry":
        now[0] = proposal.expires_at
    elif change == "resident":
        authority.hearth.save_resident(
            "reader", Declaration("Reader", "Changed", 10_000), expected_revision=1
        )
    else:
        (authority.artifacts.root / (artifact + ".md")).write_text("changed")
    assert broker.execute(proposal.id)["reason"] == reason
    assert not list((tmp_path / "noticeboard").glob("*.md"))


def test_concurrent_dispatchers_publish_only_once(system, tmp_path, monkeypatch):
    broker, proposal = approved_broker(system, tmp_path)
    original = broker.effect.publish
    calls = []

    def publish(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(broker.effect, "publish", publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(broker.execute, [proposal.id, proposal.id]))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0]["status"] == "completed"
    assert calls == [proposal.id]


def test_another_completed_publication_invalidates_old_destination_revision(system, tmp_path):
    broker, first = approved_broker(system, tmp_path)
    authority, artifact, now = system
    second = authority.request("second", artifact, expires_at=now[0] + 300)
    authority.decide(second.id, reviewed_digest=second.digest, approve=True)
    assert broker.execute(first.id)["status"] == "completed"
    assert broker.execute(second.id)["reason"] == "destination_changed"
    assert len(list((tmp_path / "noticeboard").glob("*.md"))) == 1


def test_mismatched_receipt_never_finalizes_or_releases_destination(system, tmp_path, monkeypatch):
    from hearth.broker import EffectReceipt

    broker, proposal = approved_broker(system, tmp_path)
    monkeypatch.setattr(
        broker.effect, "inspect", lambda _: EffectReceipt("another", proposal.digest)
    )
    assert broker.execute(proposal.id)["reason"] == "receipt_mismatch"
    assert broker.inspect(proposal.id)["status"] == "unknown"
    with broker.authority.hearth.database.transaction() as db:
        assert db.execute("SELECT revision FROM publication_targets").fetchone()[0] == 1


def test_schema_three_upgrade_preserves_existing_result_and_epoch(system):
    authority, artifact, _ = system
    database = authority.hearth.database
    with database.transaction(write=True) as db:
        epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
        db.execute("DROP TABLE operator_controls")
        db.execute("DROP TABLE deliveries")
        db.execute("DROP TABLE occurrences")
        db.execute("DROP TABLE routine_revisions")
        db.execute("DROP TABLE routines")
        db.execute("DROP TABLE publication_actions")
        db.execute("DROP TABLE approvals")
        db.execute("DROP TABLE publication_targets")
        db.execute("DROP TABLE publication_policies")
        db.execute("PRAGMA user_version = 3")
    database.initialize()
    with database.transaction() as db:
        assert db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0] == epoch
        assert db.execute("SELECT id FROM artifacts").fetchone()[0] == artifact
    assert propose(system).status == "pending"
