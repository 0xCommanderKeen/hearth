"""Verify packaged assets and behavior from a clean, locked release installation."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
wheel_path = max((root / "dist").glob("*.whl"), key=lambda path: path.stat().st_mtime_ns)
with ZipFile(wheel_path) as wheel:
    names = set(wheel.namelist())
    if "hearth/web/index.html" not in names:
        raise SystemExit("Browser index missing from release wheel")
    index = wheel.read("hearth/web/index.html").decode()
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', index)
    if not assets:
        raise SystemExit("Browser index references no built assets")
    for asset in assets:
        if "hearth/web" + asset not in names:
            raise SystemExit("Missing browser asset: " + asset)
print("Release wheel contains the browser and all referenced assets.", flush=True)


with tempfile.TemporaryDirectory(prefix="hearth-release-") as folder:
    isolated = Path(folder)
    environment = isolated / "venv"
    python = environment / "bin/python"
    requirements = isolated / "runtime.txt"

    def run(*args, cwd=root):
        subprocess.run(args, cwd=cwd, check=True, timeout=120)

    # Build the release environment with the pip-compatible interface: this check
    # owns a throwaway venv and installs hash-pinned exports into it, which the
    # project-level `uv sync` cannot express. A wrapper earlier on PATH may refuse
    # `uv pip` to steer people towards `uv add`, so take the first uv that answers.
    # Probe the subcommand actually used, not bare `uv pip`: a wrapper that filters
    # on the subcommand would let `uv pip --help` through and fail later regardless.
    def resolve_uv():
        for directory in os.get_exec_path():
            candidate = Path(directory) / "uv"
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            try:
                probe = subprocess.run(
                    (str(candidate), "pip", "install", "--help"),
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    timeout=60,
                )
            except OSError, subprocess.SubprocessError:
                continue
            if probe.returncode == 0:
                return str(candidate)
        raise SystemExit("No uv providing the `uv pip` interface found on PATH")

    uv = resolve_uv()
    # The Makefile builds the wheel with whichever uv comes first on PATH. Say which
    # one installs it here, so a two-installation mismatch is diagnosable.
    print("Release install uses uv at", uv, flush=True)

    run(
        uv,
        "export",
        "--quiet",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--output-file",
        str(requirements),
    )
    run(uv, "venv", str(environment), "--python", sys.executable)
    run(uv, "pip", "sync", "--python", str(python), "--require-hashes", str(requirements))
    run(uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel_path))
    run(str(python), "-I", str(root / "scripts/smoke-installed.py"), cwd=isolated)
    # The release seeds no data, so exercise the installed CLI against what the
    # smoke run left behind. Reads repeat to prove they do not mutate the store.
    for _ in range(2):
        run(
            str(python),
            "-I",
            "-m",
            "hearth",
            "show-resident",
            "--data",
            "http-data-inline_mock",
            "--resident",
            "reader",
            cwd=isolated,
        )
    run(
        str(python),
        "-I",
        "-m",
        "hearth",
        "backup",
        "--data",
        "http-data-inline_mock",
        "--destination",
        "cli-backup",
        cwd=isolated,
    )
    run(
        str(python),
        "-I",
        "-m",
        "hearth",
        "verify-backup",
        "--source",
        "cli-backup",
        cwd=isolated,
    )
