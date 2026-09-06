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


@pytest.mark.parametrize("foreign", [False, True])
def test_second_container_failure_still_cleans_owned_collector(tmp_path, foreign):
    calls = []
    ids = {"collector": "a" * 64, "cli": "b" * 64}

    def docker(*args):
        calls.append(args)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            if name == "cli":
                raise OSError("CLI create acknowledgement lost")
            return ids[name]
        if args[0] == "inspect":
            name = "collector" if args[1] == ids["collector"] else "cli"
            return json.dumps(
                [
                    {
                        "Id": ids[name],
                        "Name": "/" + name,
                        "Config": {
                            "Labels": {
                                probe.LABEL: "foreign" if foreign and name == "cli" else name
                            }
                        },
                        "HostConfig": {
                            "NetworkMode": "none",
                            "ReadonlyRootfs": True,
                            "PidMode": "",
                        },
                        "Mounts": [],
                        "State": {"Running": name == "collector"},
                    }
                ]
            )
        return ""

    with pytest.raises((OSError, AssertionError)):
        with probe.ExitStack() as stack:
            stack.enter_context(probe.owned_container(docker, tmp_path, "collector", [], []))
            stack.enter_context(probe.owned_container(docker, tmp_path, "cli", [], []))
    assert ("stop", "--time", "12", ids["collector"]) in calls
    assert ("rm", ids["collector"]) in calls
    assert (("rm", ids["cli"]) in calls) is not foreign
    assert len([call for call in calls if call[0] == "create"]) == 2
    assert (tmp_path / "collector.json").exists() and (tmp_path / "cli.json").exists()
