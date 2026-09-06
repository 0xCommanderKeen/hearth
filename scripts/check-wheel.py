"""A successful backend build must include every browser asset its HTML requests."""

import re
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
print("Release wheel contains the browser and all referenced assets.")
