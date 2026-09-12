"""Exercise installed Hearth against a synthetic Discord HTTP endpoint."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import hearth

assert Path(hearth.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert not (Path(hearth.__file__).parent / "fake_runtime.py").exists()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.discord_journey import journey  # noqa: E402

with TemporaryDirectory(prefix="hearth-discord-", dir=Path.cwd()) as temporary:
    result = journey(Path(temporary))
print(json.dumps({"installed_discord_journey": result}, sort_keys=True))
