"""Reducing a coarse pressure field to statements a forecaster would make.

The bar these tests hold: the module must produce the sentence that was
missing, and must NOT produce sentences the sampling can't support.
"""

from openlocalweather.synoptic import summarize_synoptic

# The live 2026-08-19 ring around Kisumu.
LIVE = {
    "points": [
        {"label": "centre", "mslp_hpa": [1016.0, 1015.3, 1014.5]},
        {"label": "N", "mslp_hpa": [1011.0, 1011.1, 1013.1]},
        {"label": "NE", "mslp_hpa": [1006.3, 1006.3, 1006.1]},
        {"label": "E", "mslp_hpa": [1016.0, 1015.7, 1015.4]},
        {"label": "SE", "mslp_hpa": [1018.6, 1018.4, 1018.1]},
        {"label": "S", "mslp_hpa": [1020.5, 1020.1, 1019.6]},
        {"label": "SW", "mslp_hpa": [1016.9, 1016.2, 1015.6]},
        {"label": "W", "mslp_hpa": [1014.6, 1013.2, 1012.6]},
        {"label": "NW", "mslp_hpa": [1013.3, 1011.9, 1013.2]},
    ]
}


def test_identifies_the_lowest_and_highest_directions_and_the_spread():
    s = summarize_synoptic(LIVE)
    assert s.lowest_label == "NE" and s.lowest_mslp_hpa == 1006.3
    assert s.highest_label == "S" and s.highest_mslp_hpa == 1020.5
    assert s.gradient_hpa == 14.2
    assert s.gradient_strength == "strong"


def test_reports_a_direction_where_pressure_is_falling_but_is_not_yet_lowest():
    """The 'something is building over there' signal, and the specific
    sentence that was missing: a system approaching from the west shows up
    as the west FALLING well before the west is the lowest point on the ring.
    Reporting only the currently-lowest quadrant would miss it entirely.
    """
    s = summarize_synoptic(LIVE)
    assert s.tendencies["W"] == "deepening"
    assert s.lowest_label != "W", "the point of the test: W is falling but not lowest"
    assert any("falling toward the west" in line for line in s.statements)


def test_never_claims_a_centre_a_track_or_a_front():
    """Point pressure at 12-degree spacing locates a direction. The true
    centre may sit between points or outside the ring entirely."""
    s = summarize_synoptic(LIVE)
    # Scan the CLAIM lines only — the caveat line necessarily names the very
    # things it is disclaiming, and matching it would be the test catching its
    # own disclaimer.
    caveat = "not a centre or a front"
    claims = " ".join(l for l in s.statements if caveat not in l).lower()
    for forbidden in ("centred over", "centered over", "moving at", "km/h", "front", "will reach here by"):
        assert forbidden not in claims, f"overclaimed: {forbidden}"
    # And the caveat must actually be present, not merely assumed.
    assert any(caveat in line for line in s.statements)


def test_a_flat_field_reports_a_weak_gradient_rather_than_inventing_one():
    flat = {"points": [{"label": lbl, "mslp_hpa": [1013.0, 1013.1, 1013.0]}
                       for lbl in ("centre", "N", "NE", "E", "SE", "S", "SW", "W", "NW")]}
    s = summarize_synoptic(flat)
    assert s.gradient_strength == "weak"
    assert all(t == "steady" for t in s.tendencies.values())
    assert not any("falling toward" in line for line in s.statements)


def test_small_changes_are_not_called_tendencies():
    """1 hPa over three days across a synoptic domain is noise."""
    nudge = {"points": [{"label": lbl, "mslp_hpa": [1013.0, 1012.6, 1012.2]}
                        for lbl in ("centre", "N", "NE", "E", "SE", "S", "SW", "W", "NW")]}
    assert all(t == "steady" for t in summarize_synoptic(nudge).tendencies.values())


def test_an_absent_field_reads_as_absent():
    """Must not degrade into a flat-field description, which would read as a
    measurement of a calm pattern rather than a missing one."""
    assert summarize_synoptic(None) is None
    assert summarize_synoptic({}) is None
    assert summarize_synoptic({"points": []}) is None


def test_a_mostly_failed_fetch_is_not_summarised():
    """Two usable points cannot describe a 2,600 km pattern."""
    assert summarize_synoptic({"points": [
        {"label": "centre", "mslp_hpa": [1013.0]},
        {"label": "N", "mslp_hpa": [None]},
    ]}) is None


def test_ring_geometry_is_location_agnostic_and_stays_on_the_globe():
    from openlocalweather.fetch.open_meteo import synoptic_ring_points

    # A high-latitude fork must not request an impossible latitude...
    for lat, lon, _ in synoptic_ring_points(82.0, 10.0):
        assert -90.0 <= lat <= 90.0
    # ...and one near the antimeridian must wrap rather than exceed 180.
    for lat, lon, _ in synoptic_ring_points(0.0, 175.0):
        assert -180.0 <= lon <= 180.0
    assert len(synoptic_ring_points(0.0, 0.0)) == 9
