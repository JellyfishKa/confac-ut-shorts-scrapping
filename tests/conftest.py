import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch, tmp_path):
    # Keep tests away from the real snapshot and download databases in data/.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # A TestClient used as a context manager must not start network polling or searches.
    monkeypatch.setattr(settings, "tracker_enabled", False)
    monkeypatch.setattr(settings, "discovery_enabled", False)
    # Most tests are about other things; the hype filter is tested on its own thresholds.
    monkeypatch.setattr(settings, "hype_max_age_hours", 10_000)
    monkeypatch.setattr(settings, "hype_min_speed", 0)
    monkeypatch.setattr(settings, "hype_min_growth_share", 0)
    monkeypatch.setattr(settings, "discovery_min_views", 1_000)
