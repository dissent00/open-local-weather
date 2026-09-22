"""A cap per credential, not one across a chain — ROADMAP item 170.

Raised by the operator on 2026-09-22, the day the chain first fell back:
*"calls against OpenRouter are not the same cap"*. It was one ceiling over
every vendor, so that day's eight Gemini 503 retries and three free probe
calls left five of twenty for the next morning's run — while neither
vendor's real quota had been touched and nothing had been billed at all.

THE NUMBER 20 IS NOT ARBITRARY and that is what makes a single cap wrong
rather than merely coarse: it is Google's free calendar-day allowance, which
is why `config/location.yaml` says a rolling 24h at 20 is at least as strict.
OpenRouter's free tier has entirely different limits. One number cannot hold
both, and raising it to fit the chain would quietly spend past the free tier
the whole deployment assumes.
"""
from datetime import datetime, timedelta, timezone

from pathlib import Path

import pytest

from openlocalweather.spend import SpendRecord, calls_in_window

NOW = datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc)


def _row(minutes_ago: int, provider: str, model: str) -> SpendRecord:
    return SpendRecord(
        at=NOW - timedelta(minutes=minutes_ago),
        provider=provider,
        model=model,
        purpose="forecast",
    )


LEDGER = [
    # The 15:01Z run: four Gemini attempts, then the fallback served it.
    *[_row(180 - i, "GeminiInteractionsProvider", "gemini-3.6-flash") for i in range(4)],
    _row(170, "OpenAICompatProvider", "nvidia/nemotron-3-super-120b-a12b:free"),
    # Probing a second free model costs OpenRouter, not Google.
    _row(30, "OpenAICompatProvider", "nex-agi/nex-n2.5-pro:free"),
]


def test_the_whole_ledger_still_counts_when_nothing_is_named():
    """The old behaviour, unchanged for every caller that does not ask."""
    assert calls_in_window(LEDGER, NOW) == 6


def test_one_vendor_is_counted_without_the_others():
    # Google's allowance is spent by Google's attempts and nothing else.
    assert calls_in_window(LEDGER, NOW, provider="GeminiInteractionsProvider") == 4


def test_two_entries_of_one_class_are_counted_apart():
    """THE CASE A CLASS NAME ALONE CANNOT SEE.

    Two OpenAI-compatible links are both `OpenAICompatProvider`, so counting
    by class would give them one budget they do not share in life. Every
    entry names its own model, so the pair discriminates them.
    """
    both = calls_in_window(LEDGER, NOW, provider="OpenAICompatProvider")
    assert both == 2

    one = calls_in_window(
        LEDGER, NOW,
        provider="OpenAICompatProvider",
        model="nex-agi/nex-n2.5-pro:free",
    )
    assert one == 1


def test_a_vendor_with_no_rows_has_spent_nothing():
    # The state every new link starts in, and it must not inherit another's.
    assert calls_in_window(LEDGER, NOW, provider="AnthropicProvider") == 0


def test_the_window_still_bounds_it():
    old = [_row(60 * 25, "GeminiInteractionsProvider", "gemini-3.6-flash")]
    assert calls_in_window(LEDGER + old, NOW, provider="GeminiInteractionsProvider") == 4


# ---------------------------------------------------------------------------
# The limit reaching enforcement, not just being stored
# ---------------------------------------------------------------------------


class _Spy:
    """A provider that only counts, so the cap is the thing being tested."""

    def __init__(self, model: str):
        self.model = model
        self.before_attempt = None

    def spend(self, times: int = 1):
        for _ in range(times):
            self.before_attempt()


class _Chain:
    """The shape `FallbackProvider` presents to the cap: a live child."""

    def __init__(self, *providers):
        self._providers = list(providers)
        self.active_provider = None
        self.model = providers[0].model

    @property
    def before_attempt(self):
        return self._providers[0].before_attempt

    @before_attempt.setter
    def before_attempt(self, value):
        for p in self._providers:
            p.before_attempt = value


def test_a_links_own_ceiling_reaches_enforcement(tmp_path):
    """THE FIELD MUST NOT BE INERT.

    A limit stored in config and read by nothing is this repo's most
    expensive recurring defect — three composers shipped that way in item
    159 alone. So this drives the hook rather than asserting the value.
    """
    from openlocalweather.pipeline import attach_spend_cap
    from openlocalweather.spend import SpendCapExceeded

    gemini = _Spy("gemini-3.6-flash")
    gemini.max_calls_per_24h = 2  # this vendor's own allowance
    openrouter = _Spy("nex-agi/nex-n2.5-pro:free")
    chain = _Chain(gemini, openrouter)

    attach_spend_cap(chain, tmp_path, max_calls=50, purpose="forecast")

    openrouter.max_calls_per_24h = 3

    chain.active_provider = gemini
    gemini.spend(2)
    with pytest.raises(SpendCapExceeded, match="gemini-3.6-flash"):
        gemini.spend()

    # AND THE OTHER LINK IS UNTOUCHED BY IT. This is the whole point: the
    # failing vendor exhausting its allowance must not spend the fallback's.
    #
    # The numbers are chosen so a GLOBAL count would refuse this. Five calls
    # are now in the ledger; if the cap counted them all, the fallback's
    # third would be its eighth and it would be refused.
    chain.active_provider = openrouter
    openrouter.spend(3)
    with pytest.raises(SpendCapExceeded, match="nex-agi"):
        openrouter.spend()


def test_a_link_without_one_uses_the_deployments(tmp_path):
    from openlocalweather.pipeline import attach_spend_cap
    from openlocalweather.spend import SpendCapExceeded

    plain = _Spy("some-model")
    chain = _Chain(plain)
    attach_spend_cap(chain, tmp_path, max_calls=2, purpose="forecast")

    chain.active_provider = plain
    plain.spend(2)
    with pytest.raises(SpendCapExceeded):
        plain.spend()


def test_a_configured_limit_reaches_the_built_link(tmp_path, monkeypatch):
    """Config to enforcement, end to end — the path three mutations survived.

    A number in `location.yaml` that nothing reads is this repo's most
    expensive recurring defect, so this builds the chain the way the CLI does
    and looks at what came out, rather than trusting the field exists.
    """
    from openlocalweather import cli

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    for prefix in ("OPENROUTER", "GROQ"):
        monkeypatch.setenv(f"{prefix}_API_KEY", "k")
        monkeypatch.setenv(f"{prefix}_BASE_URL", f"https://{prefix.lower()}.test/v1")
        monkeypatch.setenv(f"{prefix}_MODEL", f"{prefix.lower()}-model")

    built = cli._build_llm_provider(
        providers=[
            {"kind": "openai", "name": "openrouter", "env_prefix": "OPENROUTER",
             "max_calls_per_24h": 50},
            {"kind": "openai", "name": "groq", "env_prefix": "GROQ"},
        ],
        fallback_models=[],
    )

    openrouter, groq = built._providers
    assert openrouter.max_calls_per_24h == 50
    # ABSENT, not zero or a default copied in: the hook falls back to the
    # deployment's number, and a 0 here would refuse every call.
    assert getattr(groq, "max_calls_per_24h", None) is None


def test_the_live_config_can_carry_one(tmp_path):
    """The schema accepts it where an operator would actually write it."""
    from openlocalweather.config import load_location_config

    src = (Path(__file__).resolve().parents[1] / "config/location.yaml").read_text()
    src = src.replace(
        "    - openai\n",
        "    - kind: openai\n"
        "      name: openrouter\n"
        "      env_prefix: OPENROUTER\n"
        "      max_calls_per_24h: 50\n",
        1,
    )
    path = tmp_path / "location.yaml"
    path.write_text(src)

    cfg = load_location_config(str(path))
    entry = next(e for e in cfg.llm_providers if not isinstance(e, str))
    assert entry.max_calls_per_24h == 50
