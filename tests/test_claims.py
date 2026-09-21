

# ---------------------------------------------------------------------------
# Tile values too long for the box — ROADMAP item 7, 2026-09-21
# ---------------------------------------------------------------------------


def test_a_tile_value_within_the_box_is_not_a_finding():
    """The month before the call split, every value looked like this."""
    from openlocalweather.claims import overlong_display_values

    assert overlong_display_values({"rain_expected": "Evening Thunderstorms"}) == []
    assert overlong_display_values(
        {"rain_expected": "Isolated Evening Showers & Thunderstorms"}
    ) == []


def test_the_worst_real_value_is_a_finding():
    """2026-09-20, 149 characters of prose in a box built for a phrase, which
    is what a reader of the page actually saw."""
    from openlocalweather.claims import CLAIM_DISPLAY_TOO_LONG, overlong_display_values

    value = (
        "Dry conditions expected today with zero measurable accumulation, though "
        "scattered thunderstorm activity remains possible late afternoon into evening."
    )
    found = overlong_display_values({"rain_expected": value})
    assert len(found) == 1
    assert found[0]["kind"] == CLAIM_DISPLAY_TOO_LONG
    assert found[0]["quote"] == value
    assert "149 characters" in found[0]["detail"]
    assert "48" in found[0]["detail"]


def test_exactly_at_the_ceiling_is_inside_it():
    """The boundary is the model's own month-long habit, and 48 was reached
    by real values. A check that flagged them would fire on the good regime."""
    from openlocalweather.claims import DISPLAY_VALUE_MAX_CHARS, overlong_display_values

    assert overlong_display_values({"rain_expected": "x" * DISPLAY_VALUE_MAX_CHARS}) == []
    assert len(overlong_display_values({"rain_expected": "x" * (DISPLAY_VALUE_MAX_CHARS + 1)})) == 1


def test_both_tile_fields_are_checked_and_nothing_else():
    """`synoptic_pattern` and `mslp_trend_24h` are short strings too and are
    NOT in the grid, so bounding every short field would report a defect the
    page does not have."""
    from openlocalweather.claims import overlong_display_values

    long = "y" * 120
    found = overlong_display_values(
        {"rain_expected": long, "onset_window": long, "synoptic_pattern": long}
    )
    assert [f["detail"].split()[0] for f in found] == ["rain_expected", "onset_window"]


def test_absent_and_non_string_values_are_not_findings():
    """`onset_window` is null on a dry day, and a null is not a long string."""
    from openlocalweather.claims import overlong_display_values

    assert overlong_display_values({"rain_expected": "Dry", "onset_window": None}) == []
    assert overlong_display_values({}) == []
