"""Optional forwarding through the real Discord worker, SQLite and loopback HTTP."""

import json
import sqlite3

import pytest
from hearth.channels.chat.model import Connection, Destination
from hearth.channels.delivery.notifications import Forwarding
from hearth.channels.delivery.validation import validate
from hearth.channels.worker import Worker
from hearth.observation.notifications import Inbox, record
from hearth.residents.models import Refused
from hearth.storage.backup import capture, restore
from hearth.storage.database import SCHEMA_VERSION, Database
from hearth.work.service import Hearth

from tests.channels.test_conversations import house as house_fixture
from tests.channels.test_delivery import notice
from tests.channels.test_discord import discord as discord_fixture
from tests.channels.test_discord import worker
from tests.channels.test_worker import rows, tick

house = house_fixture
discord = discord_fixture
DEST = Destination(connection_id="discord", guild_id="200", channel_id="300")
KINDS = ["run.succeeded", "run.failed", "run.cancelled"]


def configure(running, *, revision=0, enabled=True, kinds=KINDS, url=None):
    return running.forwarding.configure(
        "notices",
        DEST,
        kinds=kinds,
        enabled=enabled,
        expected_revision=revision,
        operator_url=url,
    )


def test_automatic_payload_restart_read_and_no_model_work(house, discord):
    notice(house, identity="old")
    with worker(house, discord) as running:
        configure(running, url="https://hearth.example")
        identity = notice(house, identity="fresh")
        Inbox(house[1]).mark(identity, read=True)
        running.step()
        assert len(discord["sent"]) == 1
        assert json.loads(discord["sent"][0]["content"]) == {
            "kind": "run.succeeded",
            "resource_id": "fresh",
            "operator_url": "https://hearth.example/",
        }
    with worker(house, discord, activate=False) as running:
        tick(house, running)
        tick(house, running)
        assert len(discord["sent"]) == 1
        assert running.delivery.inspect()[0]["state"] == "confirmed"
        assert running.forwarding.inspect()[0]["operator_url"] == "https://hearth.example"
    assert not rows(house, "runs") and not rows(house, "tasks")
    assert next(n for n in rows(house, "notifications") if n["id"] == identity)["read_at"]


def test_revisions_skip_disabled_interval_empty_filters_and_deduplicate_backfill(house, discord):
    with worker(house, discord) as running:
        configure(running)
        notice(house, identity="queued")
        running.forwarding.enqueue("notices")
        configure(running, revision=1, enabled=False)
        assert running.delivery.inspect()[0]["state"] == "refused"
        notice(house, identity="disabled")
        configure(running, revision=2, kinds=[])
        notice(house, identity="empty-filter")
        running.step()
        assert not discord["sent"]
        configure(running, revision=3)
        notice(house, kind="run.cancelled", identity="new-filter")
        tick(house, running)
        tick(house, running)
        assert len(discord["sent"]) == 1
        with house[1].database.transaction() as db:
            bound = db.execute("SELECT MAX(sequence) FROM audit").fetchone()[0]
        assert running.forwarding.enqueue("notices", backfill_after=0, through_cursor=bound) == 2
        assert running.forwarding.enqueue("notices", backfill_after=0, through_cursor=bound) == 0
        assert len(running.delivery.inspect()) == 4
        with pytest.raises(Refused, match="immutable"):
            running.forwarding.configure(
                "notices",
                DEST.model_copy(update={"channel_id": "301"}),
                kinds=KINDS,
                enabled=True,
                expected_revision=4,
            )


def test_lost_ack_disable_and_held_restore_never_resend(house, discord, tmp_path):
    discord["lose_send"] = True
    with worker(house, discord) as running:
        configure(running)
        notice(house)
        running.step()
        assert running.delivery.inspect()[0]["state"] == "unknown"
        configure(running, revision=1, enabled=False)
    with worker(house, discord, activate=False) as running:
        tick(house, running)
        assert running.delivery.inspect()[0]["state"] == "unknown"
    assert len(discord["sent"]) == 1
    capture(house[1].database.path.parent, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    held = Hearth(Database(tmp_path / "restored/hearth.db"))
    with pytest.raises(Refused, match="restored_copy_read_only"):
        with Worker(held, house[8], {"discord": discord["factory"]}):
            pytest.fail("held worker started")
    with pytest.raises(Refused, match="restored_copy_read_only"):
        Forwarding(Worker(held).delivery).enqueue("notices")


@pytest.mark.parametrize("status", [401, 403, 429])
def test_transport_refusals_full_rate_delay_and_no_recursive_notices(house, discord, status):
    discord["fault"] = lambda method, path: (
        (status, {"retry_after": 1000, "global": True}, {"Retry-After": "1000"})
        if method == "POST"
        else None
    )
    with worker(house, discord) as running:
        configure(running)
        notice(house)
        running.step()
        op = running.delivery.inspect()[0]
        assert op["state"] == ("queued" if status == 429 else "refused")
        discord["fault"] = None
        tick(house, running)
        assert not discord["sent"]
        if status == 429:
            house[2][0] += 1000
            tick(house, running)
            assert len(discord["sent"]) == 1
        assert len(rows(house, "notifications")) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com",
        "https://example.com/?token=x",
        "https://example.com/#token",
        "https://example.com/secret",
        "javascript:alert(1)",
        "https://example.com:99999",
        "https://example.com\n",
        "https://example.com/%61",
    ],
)
def test_authenticated_or_unsafe_urls_never_persist(house, discord, url):
    running = worker(house, discord)
    with pytest.raises(Refused, match="operator_url_invalid"):
        configure(running, url=url)
    assert not running.forwarding.inspect()


def test_bounded_selection_rotation_missing_secret_and_idle_audit(house, discord):
    # No adapter or secret is available: enqueue remains durable and fair.
    with Worker(house[1]) as running:
        for n in range(10):
            running.forwarding.configure(
                f"binding-{n}",
                DEST.model_copy(update={"channel_id": str(300 + n)}),
                kinds=KINDS,
                enabled=True,
                expected_revision=0,
            )
        with house[1].database.transaction(write=True) as db:
            for n in range(105):
                record(db, "run.failed", f"resource-{n}", house[2][0])
        running.step()
        assert len(rows(house, "delivery_operations")) == 800
        running.step()
        assert len(rows(house, "delivery_operations")) == 1000
        # Capacity affects every binding on this connection; a separate healthy
        # connection still progresses despite the full first connection.
        house[3].save(
            "connection",
            "healthy",
            Connection(transport="discord", bot_id="healthy-bot", secret_ref="bot", state="active"),
            expected_revision=0,
        )
        running.forwarding.configure(
            "z-healthy",
            DEST.model_copy(update={"connection_id": "healthy"}),
            kinds=KINDS,
            enabled=True,
            expected_revision=0,
        )
        notice(house, identity="later")
        for _ in range(4):
            running.step()
        assert any(op["connection_id"] == "healthy" for op in rows(house, "delivery_operations"))
        # Disable all selections, which safely refuses queued records without credentials.
        for config in running.forwarding.inspect():
            running.forwarding.configure(
                config["id"],
                Destination(**config["destination"]),
                kinds=[],
                enabled=False,
                expected_revision=1,
            )
        before = len(rows(house, "audit"))
        for _ in range(4):
            running.step()
        assert len(rows(house, "audit")) == before
        assert all(op["state"] == "refused" for op in rows(house, "delivery_operations"))


def test_forward_cursor_pages_restart_and_idle_without_self_audit(house, discord):
    with Worker(house[1]) as running:
        configure(running)
        for n in range(105):
            notice(house, identity=f"paged-{n}")
        running.step()
        assert len(rows(house, "delivery_operations")) == 100
    with Worker(house[1]) as running:
        running.step()
        assert len(rows(house, "delivery_operations")) == 105
        before = len(rows(house, "audit"))
        for _ in range(5):
            running.step()
        assert len(rows(house, "audit")) == before
        with house[1].database.transaction() as db:
            validate(db)


def test_v18_upgrade_preserves_legacy_payload_digest_and_fills_origin(house, discord):
    running = worker(house, discord)
    configure(running)
    notice(house)
    running.forwarding.enqueue("notices")
    original = running.delivery.inspect()[0]
    # Reconstruct a genuine legacy immutable operation without changing its identity.
    from hearth.management.authority import digest

    with house[1].database.transaction(write=True) as db:
        intent = json.loads(db.execute("SELECT intent FROM delivery_operations").fetchone()[0])
        intent["text"] = db.execute("SELECT payload FROM notifications").fetchone()[0]
        sha = digest(intent)
        db.execute("UPDATE delivery_operations SET intent=?,sha256=?", (json.dumps(intent), sha))
        db.execute(
            "UPDATE audit SET detail=? WHERE kind='delivery.enqueued'",
            (json.dumps({"kind": "notification", "sha256": sha}),),
        )
    with sqlite3.connect(house[1].database.path) as db:
        db.execute("DROP TABLE notification_forwarding_origins")
        db.execute("ALTER TABLE notification_forwarding DROP COLUMN operator_url")
        db.execute("PRAGMA user_version=18")
    house[1].database.initialize()
    assert house[1].database.path.with_name("hearth.db.before-v18").exists()
    assert running.forwarding.inspect()[0]["operator_url"] is None
    assert running.delivery.inspect()[0]["id"] == original["id"]
    with house[1].database.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT sha256 FROM delivery_operations").fetchone()[0] == sha
        validate(db)
    notice(house, identity="after-upgrade")
    with Worker(house[1]) as upgraded:
        upgraded.step()
        assert len(upgraded.delivery.inspect()) == 2
    with house[1].database.transaction() as db:
        validate(db)


def test_connection_revocation_without_credentials_refuses_queued(house, discord):
    with Worker(house[1]) as running:
        configure(running)
        notice(house)
        running.step()
        assert running.delivery.inspect()[0]["state"] == "queued"
        house[3].save(
            "connection",
            "discord",
            Connection(transport="discord", bot_id="100", secret_ref="bot", state="disabled"),
            expected_revision=1,
        )
        running.step()
        running.step()
        assert running.delivery.inspect()[0]["state"] == "refused"
        assert not discord["sent"]


def test_config_revision_during_send_preserves_exact_confirmation(house, discord):
    with worker(house, discord) as running:
        configure(running)
        notice(house)

        def revoke(method, path):
            if method == "POST":
                configure(running, revision=1, enabled=False)
            return None

        discord["fault"] = revoke
        running.step()
        assert running.delivery.inspect()[0]["state"] == "confirmed"
        assert len(discord["sent"]) == 1
        with house[1].database.transaction() as db:
            validate(db)


def test_actual_run_notice_never_contains_instructions_output_or_owner(house, discord):
    from tests.work.test_letter_replies import bridge_of, settle

    with worker(house, discord) as running:
        configure(running)
        task = house[1].submit(
            "privacy",
            "herald",
            "private instructions and conversation",
            expires_at=house[2][0] + 600,
        )
        run = house[1].admit(task.task_id, reserve=10000)
        bridge_of(house[0], run, "privacy")
        settle(house[0], run, text="private model output synthetic-bot-secret")
        running.step()
        content = discord["sent"][0]["content"]
        assert json.loads(content) == {"kind": "run.succeeded", "resource_id": run.id}
        for protected in (run.owner_token, "private", "synthetic-bot-secret", "conversation"):
            assert protected not in content


def test_url_revisions_preserve_confirmed_unknown_and_held_backup(house, discord, tmp_path):
    with worker(house, discord) as running:
        configure(running)
        notice(house, identity="without-origin")
        running.step()
        configure(running, revision=1, url="https://first.example")
        notice(house, identity="first-origin")
        discord["lose_send"] = True
        tick(house, running)
        tick(house, running)
        configure(running, revision=2, url="https://moved.example")
        discord["lose_send"] = False
        notice(house, identity="moved-origin")
        tick(house, running)
        tick(house, running)
        assert [json.loads(s["content"]).get("operator_url") for s in discord["sent"]] == [
            None,
            "https://first.example/",
            "https://moved.example/",
        ]
        assert sorted(op["state"] for op in running.delivery.inspect()) == [
            "confirmed",
            "confirmed",
            "unknown",
        ]
        with house[1].database.transaction() as db:
            validate(db)
    capture(house[1].database.path.parent, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    held = Hearth(Database(tmp_path / "restored/hearth.db"))
    with held.database.transaction() as db:
        validate(db)
        assert len(db.execute("SELECT * FROM notification_forwarding_origins").fetchall()) == 3
