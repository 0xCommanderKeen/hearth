"""The same test-only fixture also runs against wheel and production Docker installs."""

from tests.discord_journey import journey


def test_discord_installed_journey(tmp_path):
    result = journey(tmp_path)
    assert len(result["checks"]) == 11
    assert result["runs"] == 4
    assert result["known_runs"] == 3
    assert result["turns"] == 2
    assert result["deliveries"] == 6
    assert result["unknown_deliveries"] == 1
