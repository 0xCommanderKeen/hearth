"""Explicit offline Mac host probe; never used by the application or default CI."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

IMAGE = "python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6"
LABEL = "org.hearth.offline-probe"
DOCKER = ["docker", "--host", "unix://" + str(Path.home() / ".docker/run/docker.sock")]


def docker(*args, timeout=15):
    result = subprocess.run(
        [*DOCKER, *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr[:2000]}")
    return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()


def inspect(name):
    return json.loads(docker("container", "inspect", name))[0]


def cleanup(name, root):
    """Retain and locate the ownership claim whenever cleanup cannot be proven."""
    try:
        owned = inspect(name)
        if owned["Config"].get("Labels", {}).get(LABEL) != name:
            raise RuntimeError(f"Container ownership mismatch; inspect {root}")
        try:
            (root / "state.json").write_text(json.dumps(owned["State"], indent=2))
            (root / "container.log").write_text(docker("logs", "--tail", "50", owned["Id"]))
        finally:
            docker("rm", "--force", owned["Id"])
    except BaseException:
        print(f"Cleanup unproven; ownership claim and synthetic evidence retained at {root}")
        raise


def main():
    if not __debug__ or sys.flags.optimize:
        raise RuntimeError("Run without Python optimization; acceptance assertions are required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Darwin":
        raise RuntimeError("This acceptance probe requires the selected Mac development host")
    if args.report.exists():
        raise RuntimeError("Report exists; choose a new report path")
    info = json.loads(docker("info", "--format", "{{json .}}"))
    if info["OperatingSystem"] != "Docker Desktop" or info["OSType"] != "linux":
        raise RuntimeError("Expected the selected Docker Desktop Linux VM")
    image = json.loads(docker("image", "inspect", IMAGE))[0]  # Never pull implicitly.
    name = "hearth-probe-" + uuid.uuid4().hex
    root = Path(tempfile.mkdtemp(prefix="hearth-isolation-")).resolve()
    root.chmod(0o755)  # Synthetic fixture mount; no real data is placed here.
    inputs = root / "input"
    inputs.mkdir(mode=0o755)
    outside = root / "unmounted"
    outside.mkdir()
    canaries = [outside / name for name in ("host-home", "hearth.db", "credential", "control")]
    for path in canaries:
        path.write_text("synthetic canary")
    (inputs / "context.json").write_text(
        json.dumps({"notes": ["Synthetic isolation note"], "unmounted": [str(p) for p in canaries]})
    )
    (inputs / "escape").symlink_to(canaries[0])
    shutil.copyfile(Path(__file__).with_name("probe-container-child.py"), inputs / "probe.py")
    for path in inputs.iterdir():
        if not path.is_symlink():
            path.chmod(0o444)
    claim = {"container_name": name, "label": name, "image": IMAGE}
    (root / "claim.json").write_text(json.dumps(claim))
    cid = None
    success = False
    report = {
        "macos": platform.mac_ver()[0],
        "architecture": platform.machine(),
        "daemon": info["ServerVersion"],
        "image": IMAGE,
        "image_id": image["Id"],
        "container_name": name,
        "synthetic_fixture": True,
        "real_host_probe": True,
        "host_probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "child_probe_sha256": hashlib.sha256((inputs / "probe.py").read_bytes()).hexdigest(),
    }
    try:
        cid = docker(
            "create",
            "--name",
            name,
            "--label",
            f"{LABEL}={name}",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--init",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--security-opt",
            "seccomp=builtin",
            "--pids-limit",
            "32",
            "--memory",
            "64m",
            "--memory-swap",
            "64m",
            "--cpus",
            "0.5",
            "--shm-size",
            "1m",
            "--tmpfs",
            "/scratch:rw,noexec,nosuid,nodev,size=1m,mode=1777",
            "--mount",
            f"type=bind,source={inputs},target=/input,readonly",
            "--workdir",
            "/scratch",
            "--entrypoint",
            "python3",
            IMAGE,
            "-I",
            "/input/probe.py",
        )
        (root / "container-id").write_text(cid)
        config = inspect(cid)
        assert config["Config"]["Labels"][LABEL] == name
        host = config["HostConfig"]
        assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
        assert host["Privileged"] is False and host["PidMode"] == ""
        assert host["PidsLimit"] == 32 and host["Memory"] == host["MemorySwap"] == 64 * 1024**2
        assert host["NanoCpus"] == 500_000_000
        assert host["CapDrop"] == ["ALL"] and not host["CapAdd"]
        assert config["Config"]["User"] == "65534:65534"
        assert len(config["Mounts"]) == 1
        mount = config["Mounts"][0]
        assert mount["Source"] == str(inputs) and mount["Destination"] == "/input"
        assert mount["RW"] is False
        docker("start", cid)
        deadline = time.monotonic() + 10
        while True:
            logs = docker("logs", "--tail", "50", cid)
            if logs:
                (root / "container.log").write_text(logs)
                result = json.loads(logs)
                assert result["checks"] == "passed"
                break
            if not inspect(cid)["State"]["Running"] or time.monotonic() > deadline:
                raise RuntimeError("Probe exited or timed out before reporting checks")
            time.sleep(0.1)
        descendant = result["descendant"]["pid"]
        assert result["descendant"]["sid"] != result["parent"]
        live = docker(
            "exec",
            cid,
            "python3",
            "-I",
            "-c",
            f"import os,time; from pathlib import Path; os.kill({descendant},0); "
            "p=Path('/scratch/heartbeat'); a=p.read_text(); time.sleep(.2); "
            "assert p.read_text()!=a; print('alive')",
        )
        assert live == "alive"
        docker("stop", "--time", "1", cid)
        stopped = inspect(cid)["State"]
        assert stopped["Running"] is False and stopped["Pid"] == 0
        assert stopped["Status"] == "exited" and stopped["ExitCode"] == 137
        top = subprocess.run([*DOCKER, "top", cid], capture_output=True, text=True, timeout=10)
        assert top.returncode != 0 and "not running" in top.stderr.lower()
        report.update(
            {
                "checks": result,
                "termination": stopped,
                "status": "passed",
                "controls": {
                    key: host[key]
                    for key in (
                        "NetworkMode",
                        "ReadonlyRootfs",
                        "PidsLimit",
                        "Memory",
                        "MemorySwap",
                        "NanoCpus",
                        "CapDrop",
                        "SecurityOpt",
                        "Tmpfs",
                        "ShmSize",
                    )
                },
            }
        )
        success = True
    finally:
        # Reconcile by exact random name even if create succeeded but its reply was lost.
        cleanup(name, root)
        if success:
            shutil.rmtree(root)
        else:
            print(f"Probe failed; synthetic evidence retained at {root}")
    with args.report.open("x") as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(f"Offline Mac container probe passed: {args.report}")


if __name__ == "__main__":
    main()
