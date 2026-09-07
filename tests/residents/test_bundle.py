"""Definition-only resident bundles round-trip through ordinary provisioning."""

import json

import pytest
from hearth.inputs.catalog import Inputs
from hearth.residents.bundle import Bundles, ResidentBundle, file_name
from hearth.residents.models import Refused
from hearth.residents.provisioning import Provisioning
from hearth.skills.catalog import Skills
from hearth.storage.database import Database
from hearth.work.service import Hearth


def store(path):
    db = Database(path / "hearth.db")
    db.initialize()
    return Hearth(db, clock=lambda: 1_788_640_000)


def seeded(hearth: Hearth) -> dict:
    """A resident with one skill, one input set, memory and a routine."""
    skill = Skills(hearth).save(
        "skill-1",
        name="Orchard reporting",
        description="Report harvest facts",
        instructions="# Report\n\nUse only supplied facts.",
        actor="operator",
    )
    inputs = Inputs(hearth).save(
        "input-1", name="Fictional orchard", notes=["Harvested 12 pears"], actor="operator"
    )
    return Provisioning(hearth).create(
        "setup-1",
        dict(
            name="Reporter",
            purpose="Report the fictional orchard",
            instructions="Be concise.",
            initial_memory="Remember: pears.",
            skills=[{"skill_id": skill["skill_id"], "revision": skill["revision"]}],
            execution_profile="inline_mock",
            input_sets=[{"input_set_id": inputs["input_set_id"]}],
            daily_limit=250_000,
            budget_timezone="Europe/Ljubljana",
            creation_reason="Daily report",
            manager="operator",
            routine={
                "instruction": "Report today",
                "local_time": "09:00",
                "timezone": "Europe/Ljubljana",
                "enabled": True,
            },
            first_assignment=None,
        ),
        actor="operator",
    )


def test_export_is_definition_only_and_valid(tmp_path):
    hearth = store(tmp_path)
    created = seeded(hearth)
    bundle = Bundles(hearth).export(created["resident_id"])
    ResidentBundle.model_validate(bundle)
    assert bundle["bundle_version"] == 1
    assert bundle["resident"]["name"] == "Reporter"
    assert bundle["resident"]["memory"] == "Remember: pears."
    assert bundle["resident"]["instructions"] == "Be concise."
    assert [skill["name"] for skill in bundle["skills"]] == ["Orchard reporting"]
    assert bundle["input_sets"][0]["notes"] == ["Harvested 12 pears"]
    assert bundle["routine"]["local_time"] == "09:00"
    assert bundle["management"] is None
    assert bundle["source"]["resident_id"] == created["resident_id"]
    assert "runs" not in bundle and "skill_id" not in json.dumps(bundle)
    assert (
        file_name("Fictional orchard reporter!")
        == "fictional-orchard-reporter.hearth-resident.json"
    )


def test_import_round_trip_into_fresh_store_creates_content(tmp_path):
    source = store(tmp_path / "source")
    bundle = Bundles(source).export(seeded(source)["resident_id"])
    target = store(tmp_path / "target")
    receipt = Bundles(target).import_("import-1", {"bundle": bundle})
    assert receipt["status"] == "ready"
    assert [entry["outcome"] for entry in receipt["resolution"]["skills"]] == ["created"]
    assert [entry["outcome"] for entry in receipt["resolution"]["input_sets"]] == ["created"]
    assert receipt["resolution"]["management_ignored"] is False
    again = Bundles(target).export(receipt["resident_id"])
    for key in ("resident", "skills", "input_sets", "routine"):
        expected = bundle[key]
        if key == "resident":
            expected = expected | {
                "creation_reason": again["resident"]["creation_reason"],
                "execution_profile": "inline_mock",
            }
        assert again[key] == expected
    assert again["resident"]["creation_reason"].startswith("Imported resident bundle")
    # The durable receipt is ordinary provisioning; same-key retry is identical.
    assert Provisioning(target).read("import-1")["status"] == "ready"
    retried = Bundles(target).import_("import-1", {"bundle": bundle})
    assert {k: v for k, v in retried.items() if k != "resolution"} == {
        k: v for k, v in receipt.items() if k != "resolution"
    }
    assert [entry["skill_id"] for entry in retried["resolution"]["skills"]] == [
        entry["skill_id"] for entry in receipt["resolution"]["skills"]
    ]
    assert retried["resolution"]["skills"][0]["outcome"] == "reused"


def test_import_reuses_matching_catalog_content(tmp_path):
    source = store(tmp_path / "source")
    bundle = Bundles(source).export(seeded(source)["resident_id"])
    target = store(tmp_path / "target")
    seeded(target)
    with target.database.transaction() as db:
        before = db.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        inputs_before = db.execute("SELECT COUNT(*) FROM input_sets").fetchone()[0]
    receipt = Bundles(target).import_(
        "import-2", {"bundle": bundle, "overrides": {"name": "Reporter copy"}}
    )
    assert receipt["status"] == "ready"
    assert [entry["outcome"] for entry in receipt["resolution"]["skills"]] == ["reused"]
    assert [entry["outcome"] for entry in receipt["resolution"]["input_sets"]] == ["reused"]
    with target.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == before
        assert db.execute("SELECT COUNT(*) FROM input_sets").fetchone()[0] == inputs_before
    assert target.resident(receipt["resident_id"]).declaration.name == "Reporter copy"


def test_tampered_digest_and_invalid_bundle_are_refused(tmp_path):
    source = store(tmp_path / "source")
    bundle = Bundles(source).export(seeded(source)["resident_id"])
    target = store(tmp_path / "target")
    tampered = json.loads(json.dumps(bundle))
    tampered["skills"][0]["instructions"] = "Do something else"
    with pytest.raises(Refused, match="bundle_digest_mismatch"):
        Bundles(target).import_("import-3", {"bundle": tampered})
    with pytest.raises(Refused, match="invalid_resident_bundle"):
        Bundles(target).import_("import-4", {"bundle": {"bundle_version": 2}})
    with pytest.raises(Refused, match="invalid_resident_bundle"):
        Bundles(target).import_("import-5", {"bundle": bundle, "extra": True})
    with target.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM resident_provisioning").fetchone()[0] == 0


def test_changed_bundle_under_same_key_conflicts_without_side_effects(tmp_path):
    source = store(tmp_path / "source")
    bundle = Bundles(source).export(seeded(source)["resident_id"])
    target = store(tmp_path / "target")
    Bundles(target).import_("import-6", {"bundle": bundle})
    changed = json.loads(json.dumps(bundle))
    changed["resident"]["purpose"] = "Something else"
    with pytest.raises(Refused, match="idempotency_key_conflict"):
        Bundles(target).import_("import-6", {"bundle": changed})
    with target.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM residents").fetchone()[0] == 1


def test_failed_provisioning_keeps_catalog_and_generic_retry_succeeds(tmp_path):
    source = store(tmp_path / "source")
    bundle = Bundles(source).export(seeded(source)["resident_id"])
    target = store(tmp_path / "target")
    from hearth.authority.household import Household

    policy = dict(daily_limit=10_000_000, timezone="Europe/Ljubljana", concurrency_limit=2)
    Household(target).save(**policy, resident_limit=1, expected_revision=0)
    seeded(target)  # occupies the single seat with different skill content
    with target.database.transaction(write=True) as db:
        db.execute("UPDATE skill_revisions SET sha256='0'||substr(sha256,2)")
    receipt = Bundles(target).import_("import-7", {"bundle": bundle})
    assert receipt["status"] == "failed" and receipt["reason"] == "household_resident_limit"
    with target.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 2
    Household(target).save(**policy, resident_limit=20, expected_revision=1)
    retried = Provisioning(target).retry("import-7", actor="operator")
    assert retried["status"] == "ready"
    assert retried["setup"]["skills"] == receipt["setup"]["skills"]


def test_foreign_profile_is_substituted_and_grant_is_never_applied(tmp_path):
    source = store(tmp_path / "source")
    bundle = Bundles(source).export(seeded(source)["resident_id"])
    bundle["resident"]["execution_profile"] = "codex_subscription"
    bundle["management"] = {
        "enabled": True,
        "profiles": ["codex_subscription"],
        "input_set_ids": [],
        "capabilities": ["create_residents"],
        "max_residents": 5,
        "max_daily_limit": 1_000_000,
        "max_reserve": 500_000,
        "max_calls": 64,
    }
    target = store(tmp_path / "target")
    receipt = Bundles(target).import_("import-8", {"bundle": bundle})
    assert receipt["status"] == "ready"
    assert receipt["resolution"]["execution_profile"] == {
        "requested": "codex_subscription",
        "used": "inline_mock",
    }
    assert receipt["resolution"]["management_ignored"] is True
    from hearth.management.authority import read_grant

    with target.database.transaction() as db:
        grant = read_grant(db, receipt["resident_id"])
    assert grant["revision"] == 0 and grant["enabled"] is False


def test_export_refuses_unknown_resident_and_exports_archived(tmp_path):
    hearth = store(tmp_path)
    with pytest.raises(Refused, match="resident_not_found"):
        Bundles(hearth).export("nobody")
    created = seeded(hearth)
    from hearth.residents.maintenance import LifecycleChange, Maintenance

    Maintenance(hearth).change_lifecycle(
        "archive-1",
        created["resident_id"],
        LifecycleChange(expected_revision=0, state="archived"),
    )
    assert Bundles(hearth).export(created["resident_id"])["resident"]["name"] == "Reporter"


def test_karen_fixture_imports_as_ordinary_resident(tmp_path):
    from pathlib import Path

    from hearth.management.authority import read_grant
    from hearth.residents.bundle import load_bundle_file

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "karen.hearth-resident.json"
    bundle = load_bundle_file(fixture)
    assert bundle["management"]["enabled"] is True
    target = store(tmp_path)
    receipt = Bundles(target).import_("import-karen", {"bundle": bundle})
    assert receipt["status"] == "ready"
    assert receipt["resolution"]["management_ignored"] is True
    assert receipt["resolution"]["execution_profile"]["used"] == "inline_mock"
    assert [entry["outcome"] for entry in receipt["resolution"]["skills"]] == ["created"] * 2
    with target.database.transaction() as db:
        assert read_grant(db, receipt["resident_id"])["revision"] == 0
    assert target.resident(receipt["resident_id"]).declaration.name == "Karen"
