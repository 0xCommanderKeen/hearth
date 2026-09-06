"""Verify packaged assets and behavior from a clean, locked release installation."""

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

    run(
        "uv",
        "export",
        "--quiet",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--output-file",
        str(requirements),
    )
    run("uv", "venv", str(environment), "--python", sys.executable)
    run("uv", "pip", "sync", "--python", str(python), "--require-hashes", str(requirements))
    run("uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel_path))
    for _ in range(2):
        run(str(python), "-I", "-m", "hearth", "demo", "--data", "cli-data", cwd=isolated)
    run(str(python), "-I", str(root / "scripts/smoke-installed.py"), cwd=isolated)
