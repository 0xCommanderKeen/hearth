"""Package the compiled browser beside the backend for a single release."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "web" / "dist"
destination = root / "backend" / "hearth" / "web"
if not (source / "index.html").is_file():
    raise SystemExit("Build web/dist with pnpm build first")
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(source, destination)
