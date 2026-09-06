"""The installed host namespace does not invalidate the pinned offline bundle."""

import hashlib
import json
import subprocess
import sys

from hearth.integrations.codex import assets


def test_existing_collector_bundle_reopens_and_imports_without_host_package(tmp_path):
    root = tmp_path / "assets"
    package = root / "app/hearth"
    assets.write_collector(package)
    expected = {
        "codex_events.py": "f0dadc3c408ab4f961536b3062d04d07ad40ea2496e972305e589f635eeb752e",
        "codex_pricing.py": "0050248e30f8b9265cafe03a7e2b1acefc94e013bcd4f66841bb2e9aca443a68",
        "codex_usage.py": "1a832247081d240f3935ab649c00c7dac3acc3957d37e78d0dc6c0099d931fc9",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((package / name).read_bytes()).hexdigest() == digest
    (root / "fixture.py").write_bytes(assets.fixture_source())
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    manifest = {"archive": assets.ARCHIVE_SHA512, "files": files}
    (root / "manifest.json").write_text(json.dumps(manifest))
    before = (root / "manifest.json").read_bytes()
    assert (
        assets.prepare(root, None)
        == hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    )
    assert (root / "manifest.json").read_bytes() == before
    # An isolated interpreter cannot accidentally resolve imports via the checkout.
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(root / 'app')!r}); "
            "from hearth.codex_usage import UsageJournal; "
            "from hearth.codex_pricing import MODEL; assert MODEL == 'gpt-6-astra'",
        ],
        check=True,
        cwd=tmp_path,
    )
