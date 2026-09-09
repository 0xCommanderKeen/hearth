"""Configuring the Claude subscription pins a binary, a version and a private login.

The CLI is never really spawned: a synthetic executable answers `--version` and
`auth status --json` the way `docs/claude-runtime.md` records the real one does.
"""

import json
import sys
from dataclasses import replace

import pytest
from hearth.app import create_app
from hearth.integrations.claude.config import KIND, MODEL, VERSION, environment
from hearth.integrations.claude.subscription import ClaudeLiveRuntime
from hearth.residents.models import Refused
from hearth.storage.database import Database


def synthetic_cli(path, *, version=VERSION, logged_in=True, body=""):
    status = json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai" if logged_in else "none"})
    path.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"if '--version' in sys.argv:\n print({version!r})\n sys.exit()\n"
        "if sys.argv[1:3] == ['auth', 'status']:\n"
        f" print({status!r})\n sys.exit()\n"
        f"{body}"
        "sys.exit(1)\n"
    )
    path.chmod(0o700)
    return path


def store(tmp_path):
    data = tmp_path / "data"
    database = Database(data / "hearth.db")
    database.initialize()
    with database.transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (KIND,))
    return data, database


def configured(tmp_path, **cli):
    data, database = store(tmp_path)
    binary = synthetic_cli(tmp_path / "synthetic-claude", **cli)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    return data, database, binary, config_dir


def pins(database):
    with database.transaction() as db:
        binary = db.execute(
            "SELECT value FROM system_meta WHERE key='claude_live_binary'"
        ).fetchone()
        audits = db.execute(
            "SELECT detail FROM audit WHERE kind='runtime.claude_subscription_configured'"
        ).fetchall()
    return (binary[0] if binary else None), [json.loads(row[0]) for row in audits]


def test_a_configured_subscription_pins_its_binary_once_with_the_version_and_model(tmp_path):
    data, database, binary, config_dir = configured(tmp_path)
    runtime = ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    assert (runtime.kind, runtime.version) == (KIND, 1)
    pinned, audits = pins(database)
    assert len(pinned) == 64
    assert audits == [{"binary": pinned, "version": VERSION, "model": MODEL}]
    # Configuring the same binary again is the same store, not a second pin.
    ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    assert pins(database) == (pinned, audits)


def test_a_missing_binary_or_config_dir_is_a_configuration_refusal(tmp_path):
    data, database, binary, config_dir = configured(tmp_path)
    for arguments in ({}, {"binary": binary}, {"config_dir": config_dir}):
        with pytest.raises(Refused, match="claude_subscription_configuration_required"):
            ClaudeLiveRuntime(data, **arguments)
    assert pins(database) == (None, [])


def test_an_unpinned_cli_version_refuses_and_pins_nothing(tmp_path):
    data, database, binary, config_dir = configured(tmp_path, version="2.1.999 (Claude Code)")
    with pytest.raises(Refused, match="claude_subscription_version_unsupported"):
        ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    assert pins(database) == (None, [])


@pytest.mark.parametrize("logged_in", [False, None])
def test_a_config_dir_without_a_login_refuses_and_pins_nothing(tmp_path, logged_in):
    data, database, binary, config_dir = configured(tmp_path, logged_in=logged_in)
    with pytest.raises(Refused, match="claude_subscription_login_required"):
        ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    assert pins(database) == (None, [])


def test_a_config_dir_that_does_not_exist_refuses_before_the_cli_can_create_one(tmp_path):
    data, database, binary, _ = configured(tmp_path)
    missing = tmp_path / "never-seeded"
    with pytest.raises(Refused, match="claude_subscription_login_required"):
        ClaudeLiveRuntime(data, binary=binary, config_dir=missing)
    assert not missing.exists()
    assert pins(database) == (None, [])


def test_a_binary_that_cannot_answer_is_a_configuration_refusal(tmp_path):
    data, database, _, config_dir = configured(tmp_path)
    unrunnable = synthetic_cli(tmp_path / "unrunnable-claude")
    unrunnable.chmod(0o600)
    for path in (tmp_path / "not-installed", unrunnable):
        with pytest.raises(Refused, match="claude_subscription_configuration_required"):
            ClaudeLiveRuntime(data, binary=path, config_dir=config_dir)
    assert pins(database) == (None, [])


def test_an_unreadable_login_answer_refuses_rather_than_assuming_a_login(tmp_path):
    data, database, binary, config_dir = configured(tmp_path)
    broken = synthetic_cli(tmp_path / "broken-claude")
    broken.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"if '--version' in sys.argv:\n print({VERSION!r})\n sys.exit()\n"
        "print('not json at all')\nsys.exit(0)\n"
    )
    broken.chmod(0o700)
    with pytest.raises(Refused, match="claude_subscription_login_required"):
        ClaudeLiveRuntime(data, binary=broken, config_dir=config_dir)
    assert pins(database) == (None, [])


def test_a_different_binary_behind_the_pin_refuses_and_keeps_the_original(tmp_path):
    data, database, binary, config_dir = configured(tmp_path)
    ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    pinned, audits = pins(database)
    other = synthetic_cli(tmp_path / "other-claude", body="# a different build\n")
    with pytest.raises(Refused, match="claude_subscription_binary_changed"):
        ClaudeLiveRuntime(data, binary=other, config_dir=config_dir)
    assert pins(database) == (pinned, audits)


def test_the_session_environment_carries_no_machine_state(tmp_path):
    env = environment(tmp_path / "claude-config")
    assert set(env) == {"PATH", "CLAUDE_CONFIG_DIR", "DISABLE_AUTOUPDATER"}
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-config")
    # The pinned binary cannot change under its own pin mid-run.
    assert env["DISABLE_AUTOUPDATER"] == "1"


def test_a_run_this_store_never_admitted_is_not_launched(tmp_path):
    data, _, binary, config_dir = configured(tmp_path)
    runtime = ClaudeLiveRuntime(data, binary=binary, config_dir=config_dir)
    with pytest.raises(Refused, match="runtime_identity_conflict"):
        runtime.start("run", "{}")
    # Nothing was created for it, so there is nothing to observe or to stop.
    assert not (data / "claude-live/run").exists()
    assert runtime.inspect("run").status == "absent"
    runtime.stop("run")
    assert not (data / "claude-live/run").exists()


def test_the_application_builds_the_adapter_its_own_store_records(tmp_path):
    data, _, binary, config_dir = configured(tmp_path)
    app = create_app(
        data,
        "operator-token-16+",
        supervise=False,
        claude_binary=binary,
        claude_config_dir=config_dir,
    )
    assert set(app.state.executor.runtimes) == {KIND}
    # Unconfigured, it refuses by name rather than falling back to the other runtime.
    with pytest.raises(Refused, match="claude_subscription_configuration_required"):
        create_app(data, "operator-token-16+", supervise=False)


def synthetic_codex(tmp_path):
    """The Codex half of a host configured for both providers at once."""
    from hearth.integrations.codex.subscription import VERSION as CODEX_VERSION

    binary = tmp_path / "synthetic-codex"
    binary.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"if '--version' in sys.argv:\n print({CODEX_VERSION!r})\n sys.exit()\n"
        "sys.exit(1)\n"
    )
    binary.chmod(0o700)
    auth = tmp_path / "synthetic-auth"
    auth.mkdir()
    (auth / "auth.json").write_text("{}")
    return binary, auth


def test_a_host_configured_for_both_providers_builds_both_beside_the_default(tmp_path):
    """The store's default is one of them; the other is there for its own residents."""
    from hearth.integrations.codex.subscription import KIND as CODEX_KIND

    data, database, binary, config_dir = configured(tmp_path)
    with database.transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (CODEX_KIND,))
    codex_binary, auth = synthetic_codex(tmp_path)
    both = dict(
        codex_binary=codex_binary,
        codex_auth_home=auth,
        claude_binary=binary,
        claude_config_dir=config_dir,
    )
    app = create_app(data, "operator-token-16+", supervise=False, **both)
    assert set(app.state.executor.runtimes) == {CODEX_KIND, KIND}

    # A host with no Claude configuration simply has no Claude runtime; the store
    # still opens, and a run pinned to Claude waits rather than launching here.
    app = create_app(
        data, "operator-token-16+", supervise=False, codex_binary=codex_binary, codex_auth_home=auth
    )
    assert set(app.state.executor.runtimes) == {CODEX_KIND}


def test_a_store_on_a_live_kind_with_no_adapter_refuses_rather_than_opening_on_another(
    tmp_path, monkeypatch
):
    """A third live kind must not quietly get another provider's adapter."""
    from hearth.integrations import interface

    data, _, binary, config_dir = configured(tmp_path)
    monkeypatch.setitem(
        interface.RUNTIMES,
        "invented_subscription",
        replace(interface.RUNTIMES[KIND], kind="invented_subscription", module=None, runtime=None),
    )
    with Database(data / "hearth.db").transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value='invented_subscription' WHERE key='runtime_kind'")
    with pytest.raises(Refused, match="runtime_configuration_invalid"):
        create_app(
            data,
            "operator-token-16+",
            supervise=False,
            claude_binary=binary,
            claude_config_dir=config_dir,
        )
