"""Lightning as its own variable — ROADMAP item 65.

The item's argument in one line: "did it storm" and "did it rain" are
different questions with different answers, and the ledger has had one column
for both. These tests pin the separation, because the field looks like an
obvious omission from `observed_convection()` to anyone who finds it without
the reasoning.
"""

from openlocalweather.models import DailyActual


def test_lightning_is_three_valued_and_starts_unasked():
    """None means nothing was asked; False would mean something looked and
    detected none. Every stored day is None today and stays None until a
    detection source exists — which is the honest state, not a gap to fill."""
    assert DailyActual(rain=False).lightning is None
    assert DailyActual(rain=False, lightning=False).lightning is False
    assert DailyActual(rain=False, lightning=True).lightning is True


def test_lightning_does_not_make_a_day_wet():
    """The whole point of adding it as a variable rather than an OR term.

    observed_convection() is `rain OR thunder OR precipitation`, so every term
    added to it can only CREATE wet days and can only move the rain rate —
    item 53.1 moved every model about five points in a day by adding one
    source. A day with lightning and no rain is a day that stormed and did not
    rain, and the record should be able to say both.
    """
    dry_storm = DailyActual(rain=False, thunder=None, precipitation=None, lightning=True)

    assert dry_storm.lightning is True
    assert dry_storm.observed_convection() is False, (
        "lightning must not join the OR — see ROADMAP item 65 and item 45's OR problem"
    )


def test_the_existing_convection_terms_are_untouched():
    """The guard on the guard. A change that added lightning to the OR would
    otherwise be caught only by the test above, and a future edit that also
    'fixed' that test would pass."""
    assert DailyActual(rain=True).observed_convection() is True
    assert DailyActual(rain=False, thunder=True).observed_convection() is True
    assert DailyActual(rain=False, precipitation=True).observed_convection() is True
    assert DailyActual(rain=False).observed_convection() is False


def test_a_dry_storm_is_distinguishable_from_a_quiet_day():
    """What the separation buys. Before this, both of these were 'not wet'
    and the record could not tell them apart."""
    quiet = DailyActual(rain=False, lightning=False)
    stormy = DailyActual(rain=False, lightning=True)

    assert quiet.observed_convection() == stormy.observed_convection()
    assert quiet.lightning != stormy.lightning


def test_lightning_survives_a_round_trip():
    """It has to reach the record, not just the model. A field that
    round-trips as None would be indistinguishable from one never added."""
    stored = DailyActual(rain=False, lightning=True).model_dump()

    assert stored["lightning"] is True
    assert DailyActual.model_validate(stored).lightning is True
