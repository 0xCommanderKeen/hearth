"""Measure what a granted folder really is inside the sandbox. Never in CI.

Slice D of epic #183 (issue #187) says a resident reaches exactly the folders its
grant names, read-only unless the grant says `rw`, and that a writable one is audited
when a run uses it. Three of those words are claims about a container runtime rather
than about Hearth, and this script measures them against a real daemon -- Docker
Desktop's Linux VM on a Mac is one, and so is any Linux host -- through the very code
Hearth ships: `granted()` turns a grant's mounts into `Mount`s, and `ContainerLauncher`
turns those into the argv.

    uv run --frozen python scripts/measure-mounts.py --report docs/evidence/mounts-<date>.json

What it measures:

1. the daemon and kernel it is talking to;
2. what `/mounts` holds inside the container: the granted names and nothing else;
3. that a read-only mount can be read and a write into it is refused by the kernel;
4. that a writable mount takes the write, and the file is on the host afterwards,
   owned by the uid Hearth runs as;
5. that Hearth's own survey, taken before and after, says the writable folder was
   written and says nothing at all about the read-only one -- which is the fact
   `run.mount_rw_used` is settled from;
6. that nothing of the host outside the grant is reachable: the folder the mounts were
   carved out of does not exist inside the container.

The image is built here, from the same pinned base and with the same user entry as
`deploy/Dockerfile.sandbox`, and without the provider CLIs -- what is measured is the
runtime's treatment of a bind mount and the uid the session runs as, neither of which
the CLIs take part in, and no CI or laptop host has the pinned Linux builds. The image
and every container are removed at the end, by id and never by Hearth's own label,
because that label is on every production sandbox and this may be run on a host that is
serving work. It touches no Hearth data directory and needs no credential.
"""

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from hearth.integrations.launcher import (  # noqa: E402
    CONTAINER,
    ContainerLauncher,
    Placement,
    granted,
    survey,
    used,
)

# The base `deploy/Dockerfile.sandbox` is built on, by digest, so what is measured here
# is the same filesystem the real image is.
BASE = "python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6"
PROBE = """
ARG BASE
FROM ${BASE}
ARG HEARTH_UID
ARG HEARTH_GID
RUN groupadd --gid ${HEARTH_GID} --non-unique hearth \\
 && useradd --uid ${HEARTH_UID} --gid ${HEARTH_GID} --non-unique --no-create-home \\
    --home-dir /nonexistent --shell /usr/sbin/nologin hearth
USER ${HEARTH_UID}:${HEARTH_GID}
WORKDIR /workspace
ENTRYPOINT []
"""
STARTED: list[str] = []


def client(docker: str, *arguments: str, timeout: float = 300) -> str:
    result = subprocess.run(
        [docker, *arguments], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"docker {arguments[0]} failed: {result.stderr[:2000]}")
    return result.stdout.strip()


def daemon(docker: str) -> dict:
    version = json.loads(client(docker, "version", "--format", "{{json .}}"))
    return {
        "client_version": version["Client"]["Version"],
        "server_version": version["Server"]["Version"],
        "server_os": version["Server"]["Os"],
        "server_arch": version["Server"]["Arch"],
        "kernel": version["Server"]["KernelVersion"],
        "host": platform.platform(),
    }


def build(docker: str, directory: Path, tag: str) -> str:
    """The sandbox image's base and its user, with no provider CLI in it."""
    (directory / "Dockerfile").write_text(PROBE)
    import os

    client(
        docker,
        "build",
        "--build-arg",
        f"BASE={BASE}",
        "--build-arg",
        f"HEARTH_UID={os.getuid()}",
        "--build-arg",
        f"HEARTH_GID={os.getgid()}",
        "--tag",
        tag,
        str(directory),
    )
    return client(docker, "image", "inspect", "--format", "{{.Id}}", tag)


def session(launcher: ContainerLauncher, program: str, mounts, workspace: Path) -> dict:
    """One container, with these mounts, running this program. What it said and its code."""
    handle = launcher.start(
        ["python3", "-c", program],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        mounts=mounts,
    )
    if launcher.identify(handle):
        STARTED.append(str(handle.id))
    assert handle.stdout is not None
    output = handle.stdout.read().decode("utf-8", errors="replace")
    return {"exit_code": launcher.wait(handle, 120), "stdout": output.strip()}


# What the session inside the container is asked to do: look at what it was given, try
# to write to both folders, and say what happened. Nothing here is Hearth's -- it is
# the kernel's answer, reported.
PROGRAM = """
import json, os
answer = {"listing": sorted(os.listdir("/mounts")), "uid": os.getuid()}
answer["read_only_content"] = open("/mounts/notes/note.md").read()
for name in ("notes", "drafts"):
    try:
        with open("/mounts/%s/written-inside.md" % name, "w") as handle:
            handle.write("the session wrote this")
        answer[name] = "written"
    except OSError as error:
        answer[name] = "%s: %s" % (type(error).__name__, error.strerror)
answer["parent_visible"] = os.path.exists(PARENT)
print(json.dumps(answer))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--network", default="none")
    parser.add_argument("--tag", default="hearth/mount-probe:measure")
    arguments = parser.parse_args()
    if arguments.report.exists():
        raise RuntimeError("Report exists; choose a new report path")
    directory = Path(tempfile.mkdtemp(prefix="hearth-mounts-"))
    workspace = directory / "workspace"
    workspace.mkdir()
    build_context = directory / "image"
    build_context.mkdir()
    notes, drafts = directory / "notes", directory / "drafts"
    notes.mkdir()
    drafts.mkdir()
    (notes / "note.md").write_text("A synthetic note, read-only.\n")
    report: dict = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base": BASE,
        "grant": [
            {"name": "notes", "host_path": str(notes), "mode": "ro"},
            {"name": "drafts", "host_path": str(drafts), "mode": "rw"},
        ],
    }
    try:
        report["daemon"] = daemon(arguments.docker)
        report["image_id"] = build(arguments.docker, build_context, arguments.tag)
        launcher = ContainerLauncher(arguments.tag, arguments.network, docker=arguments.docker)
        placement = Placement(CONTAINER)
        reached = granted(placement, report["grant"])
        report["arguments"] = [mount.argument() for mount in placement.mounts]
        folders = {"notes": str(notes), "drafts": str(drafts)}
        before = {name: survey(path) for name, path in folders.items()}
        report["session"] = session(
            launcher,
            PROGRAM.replace("PARENT", json.dumps(str(directory))),
            placement.mounts,
            workspace,
        )
        inside = json.loads(report["session"]["stdout"].splitlines()[-1])
        report["inside"] = inside
        after = {name: survey(path) for name, path in folders.items()}
        # What Hearth would write on the receipt, from the same survey the worker takes.
        report["receipt_mounts"] = used(
            reached,
            {name: before[name] for name in ("drafts",)},
            {name: after[name] for name in ("drafts",)},
        )
        report["host_after"] = {
            "notes": sorted(item.name for item in notes.iterdir()),
            "drafts": sorted(item.name for item in drafts.iterdir()),
            "drafts_owner": (drafts / "written-inside.md").stat().st_uid
            if (drafts / "written-inside.md").exists()
            else None,
        }
        # The read-only folder is surveyed here only to show that it did not change;
        # the worker never surveys one, which is why its receipt entry is `null`.
        report["survey"] = {name: before[name] != after[name] for name in folders}
    finally:
        if STARTED:
            subprocess.run(
                [arguments.docker, "rm", "--force", *STARTED],
                capture_output=True,
                check=False,
                timeout=120,
            )
        subprocess.run(
            [arguments.docker, "image", "rm", "--force", arguments.tag],
            capture_output=True,
            check=False,
            timeout=120,
        )
        shutil.rmtree(directory, ignore_errors=True)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
