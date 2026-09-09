from pathlib import Path

import pytest

from openlocalweather.config import SecondaryPoint, load_location_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_example_config():
    cfg = load_location_config(REPO_ROOT / "config" / "location.example.yaml")
    assert cfg.timezone == "UTC"
    assert cfg.secondary_point.enabled is False


def test_load_real_kisumu_config():
    cfg = load_location_config(REPO_ROOT / "config" / "location.yaml")
    assert cfg.primary_place_name == "Kisumu, Kenya"
    assert cfg.timezone == "Africa/Nairobi"
    assert cfg.secondary_point.enabled is True
    # Moved 2026-09-09 from open Lake Victoria 194 km away to the Winam Gulf
    # off the city — the old point forecast an 18.5 C high against the gulf's
    # 28.0 C on the same day, so the boaters' section had been describing a
    # different climate. Pinned by name AND position, because the position is
    # the part that was wrong and a rename alone would not have caught it.
    assert cfg.secondary_point.name == "Winam Gulf"
    assert (cfg.secondary_point.lat, cfg.secondary_point.lon) == (-0.15, 34.65)
    assert len(cfg.region_points) == 4
    assert cfg.primary_point.lat == pytest.approx(-0.0917)


def test_missing_config_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_location_config(missing)


def test_malformed_config_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not_a_location_key: true\n")
    with pytest.raises(ValueError):
        load_location_config(bad)


def test_secondary_point_constructs_with_no_args():
    # Regression test: SecondaryPoint's zero-arg default (used as
    # LocationConfig's default_factory when a location.yaml omits the
    # secondary_point block entirely) must not require lat/lon.
    sp = SecondaryPoint()
    assert sp.enabled is False
    assert sp.lat == 0.0
    assert sp.lon == 0.0
