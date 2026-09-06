"""Probe ownership recovery without a Docker daemon or real credentials."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/probe-codex-subscription.py"
spec = importlib.util.spec_from_file_location("subscription_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("failure", ["lost_reply", "invalid_reply", "foreign", "daemon_down"])
def test_uncertain_create_uses_recorded_claim_and_never_relaunches(tmp_path, monkeypatch, failure):
    calls = []
    name = None
    cid = "a" * 64

    def docker(*args):
        nonlocal name
        calls.append(args)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            claim = json.loads((tmp_path / (name + ".json")).read_text())
            assert claim["name"] == name and claim["label"] == probe.LABEL
            if failure == "invalid_reply":
                return "not-an-id"
            raise OSError("create reply lost")
        if args[0] == "inspect":
            assert args[1] == name
            if failure == "daemon_down":
                raise OSError("daemon unavailable")
            return json.dumps(
                [
                    {
                        "Id": cid,
                        "Name": "/" + name,
                        "Config": {
                            "Labels": {probe.LABEL: "foreign" if failure == "foreign" else name}
                        },
                        "State": {"Running": False},
                    }
                ]
            )
        assert args == ("rm", cid)
        return ""

    monkeypatch.setattr(probe, "LocalDocker", lambda: docker)
    with pytest.raises((OSError, AssertionError)):
        probe.run_case(tmp_path / "vendor", tmp_path / "child", False, tmp_path)
    assert [call[0] for call in calls] == (
        ["create", "inspect", "rm"]
        if failure in {"lost_reply", "invalid_reply"}
        else ["create", "inspect"]
    )
    assert (tmp_path / (name + ".json")).exists()
