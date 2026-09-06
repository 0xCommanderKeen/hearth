"""Cleanup failure diagnostics without a Docker daemon or process launch."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).parents[1] / "scripts/probe-mac-container.py"
    spec = importlib.util.spec_from_file_location("container_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", ["inspect", "remove"])
def test_daemon_loss_preserves_and_reports_claim(probe, tmp_path, monkeypatch, capsys, failure):
    claim = tmp_path / "claim.json"
    claim.write_text('"synthetic ownership claim"')
    calls = []

    def docker(*args):
        calls.append(args)
        if args[0] == "container" and failure == "inspect" or args[0] == "rm":
            raise RuntimeError("injected daemon loss")
        if args[0] == "container":
            return json.dumps(
                [{"Id": "owned-id", "Config": {"Labels": {probe.LABEL: "owned"}}, "State": {}}]
            )
        return "synthetic log"

    monkeypatch.setattr(probe, "docker", docker)
    with pytest.raises(RuntimeError, match="injected daemon loss"):
        probe.cleanup("owned", tmp_path)
    assert claim.read_text() == '"synthetic ownership claim"'
    assert str(tmp_path) in capsys.readouterr().out
    removals = [call for call in calls if call[0] == "rm"]
    assert removals == ([] if failure == "inspect" else [("rm", "--force", "owned-id")])


def test_wrong_ownership_never_removes_container(probe, tmp_path, monkeypatch, capsys):
    calls = []

    def docker(*args):
        calls.append(args)
        return '[{"Id":"foreign","Config":{"Labels":{"org.hearth.offline-probe":"other"}}}]'

    monkeypatch.setattr(probe, "docker", docker)
    with pytest.raises(RuntimeError, match="ownership mismatch"):
        probe.cleanup("owned", tmp_path)
    assert calls == [("container", "inspect", "owned")]
    assert str(tmp_path) in capsys.readouterr().out
