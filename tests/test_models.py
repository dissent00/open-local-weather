"""Hand-computed expectations for the domain models.

Layer one of the three-layer contract in spec/README.md: these say Python is
RIGHT, where the vectors only say the two implementations AGREE.
"""

import re

from openlocalweather.models import format_temp_high_low


def test_temp_high_low_is_computed_not_transcribed():
    """The live regression. A blended high of 33.5 C was published as
    "34C / 93F" by the model, which rounded to 34 and converted that. 33.5 C
    is 92.3 F, so a third of the apparent jump from yesterday's 90 F was
    invented in the rounding."""
    assert format_temp_high_low(33.5, 18.0) == "34\u00b0C / 92\u00b0F high, 18\u00b0C / 64\u00b0F low"


def test_each_unit_is_rounded_from_the_true_value():
    """And so the pair deliberately does not round-trip: 34 C is 93.2 F, but
    the true 33.5 C is 92.3 F and 92 is the closest whole number to it.
    Rounding twice is the bug this replaced, not a property to restore."""
    assert "34\u00b0C / 92\u00b0F" in format_temp_high_low(33.5, 18.0)


def test_format_is_fixed_rather_than_reinvented_each_day():
    """Two consecutive live days produced two different formats, because
    nothing had ever fixed one: "32C / 90F (High) | 18C / 64F (Low)" on the
    26th and "34C / 93F high, 18C / 64F low" on the 27th. The shape is now the
    same whatever the numbers are."""
    shape = re.compile(
        r"^-?\d+\u00b0C / -?\d+\u00b0F high, -?\d+\u00b0C / -?\d+\u00b0F low$"
    )
    for high, low in [(32.3, 18.0), (33.5, 18.5), (-2.5, -7.5), (40.0, 25.0)]:
        assert shape.match(format_temp_high_low(high, low)), (high, low)
