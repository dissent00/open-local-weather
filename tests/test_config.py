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


# --- reporting bands — ROADMAP item 145 --------------------------------------


def test_an_unconfigured_deployment_gets_the_shipped_bands(tmp_path):
    """Omitting the block is the normal case and must change nothing."""
    from openlocalweather.config import deviation_bands
    from openlocalweather.models import DeviationBands

    cfg = load_location_config("config/location.yaml")
    assert deviation_bands(cfg) == DeviationBands()


def test_one_configured_band_does_not_drag_the_other_with_it(tmp_path):
    """FIELD BY FIELD, not all-or-nothing. A deployment tightening the
    near-freezing band must not silently inherit a stale value for the other,
    and `None` means not-configured rather than zero."""
    import yaml

    from openlocalweather.config import deviation_bands
    from openlocalweather.models import DeviationBands

    raw = yaml.safe_load(open("config/location.yaml").read())
    raw["location"]["deviation_bands"] = {"low_freezing_c": 0.5}
    path = tmp_path / "location.yaml"
    path.write_text(yaml.safe_dump(raw))

    got = deviation_bands(load_location_config(path))
    assert got.low_freezing_c == 0.5, "the configured field is honoured"
    assert got.low_c == DeviationBands().low_c, "the unset one keeps its default"


def test_a_configured_band_cannot_change_what_the_run_spends(tmp_path):
    """The config-level restatement of item 145's safety property. The test in
    test_disagreement.py sweeps the value; this one proves the wiring cannot
    reach the spending decision either."""
    import yaml

    from openlocalweather.config import deviation_bands
    from openlocalweather.disagreement import observation_disagreements
    from openlocalweather.models import ObservedSoFar
    from openlocalweather.disagreement import StandingCall

    standing = StandingCall(temp_low_c=-0.5)
    observed = ObservedSoFar(low_c=2.0)
    baseline = observation_disagreements(standing, observed, low_is_settled=True)

    raw = yaml.safe_load(open("config/location.yaml").read())
    raw["location"]["deviation_bands"] = {"low_c": 0.1, "low_freezing_c": 0.1}
    path = tmp_path / "location.yaml"
    path.write_text(yaml.safe_dump(raw))

    got = observation_disagreements(
        standing, observed, low_is_settled=True,
        bands=deviation_bands(load_location_config(path)),
    )
    assert got == baseline


def test_no_scored_path_can_see_a_reporting_band():
    """THE RECORD MUST NOT MOVE WITH THE READER'S SETTINGS — ROADMAP item 145.

    A band changes what a deployment SAYS. If it could change what is SCORED,
    two deployments would disagree about the accuracy of the same forecast and
    the cross-deployment record — this project's strongest claim — would stop
    meaning anything.

    STRUCTURAL RATHER THAN BEHAVIOURAL, deliberately. A test that scored one
    forecast under two band settings and compared the numbers would pass today
    for the trivial reason that nothing is wired, and would keep passing until
    someone wired it wrongly in a case the test did not happen to cover. This
    asserts the thing that must stay true: the scoring code cannot SEE a band
    at all.
    """
    import ast
    from pathlib import Path

    forbidden = {"DeviationBands", "deviation_bands", "bands"}
    scored = {
        Path("src/openlocalweather/verify/scoring.py"): None,
        Path("src/openlocalweather/pipeline.py"): {
            "_blend_prediction",
            "_extended_blend_predictions",
        },
    }

    offences = []
    for path, only in scored.items():
        tree = ast.parse(path.read_text())
        nodes = []
        if only is None:
            nodes = [tree]
        else:
            nodes = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name in only
            ]
            assert len(nodes) == len(only), f"{path}: a scored builder was renamed"

        for node in nodes:
            for sub in ast.walk(node):
                name = getattr(sub, "id", None) or getattr(sub, "arg", None)
                if name in forbidden:
                    offences.append(f"{path.name}:{sub.lineno} references {name!r}")

    assert not offences, (
        "a reporting band reached the scored record: "
        + "; ".join(offences)
        + ". Bands decide what a reader is TOLD, never what the record SCORES."
    )


def test_an_acknowledgement_carries_the_date_it_was_decided(tmp_path):
    """ROADMAP item 152 step 4: an exclusion made on a measurement should say
    when. Optional, because a fork's entries may predate the field."""
    import datetime as _dt

    import yaml

    raw = yaml.safe_load(open("config/location.yaml").read())
    gaps = raw["location"]["acknowledged_coverage_gaps"]
    gaps[0]["since"] = "2026-08-20"
    gaps[1].pop("since", None)
    path = tmp_path / "location.yaml"
    path.write_text(yaml.safe_dump(raw))

    cfg = load_location_config(path)
    assert cfg.acknowledged_coverage_gaps[0].since == _dt.date(2026, 8, 20)
    assert cfg.acknowledged_coverage_gaps[1].since is None


def test_every_acknowledgement_in_the_real_config_is_dated():
    """The dates come from `git log -S` on the file, not from memory: six
    entries on 2026-08-20, thirteen on 2026-09-07. A new entry without one is
    an exclusion that has already lost the measurement behind it."""
    cfg = load_location_config(REPO_ROOT / "config" / "location.yaml")
    assert cfg.acknowledged_coverage_gaps, "the reference deployment has acknowledgements"
    undated = [g for g in cfg.acknowledged_coverage_gaps if g.since is None]
    assert undated == []


def test_the_high_and_onset_bands_resolve_field_by_field(tmp_path):
    """Item 145's next step: the same rule as the low bands. An unset field
    keeps the shipped default rather than inheriting its neighbour."""
    import yaml

    from openlocalweather.config import deviation_bands
    from openlocalweather.models import DeviationBands

    raw = yaml.safe_load(open("config/location.yaml").read())
    raw["location"]["deviation_bands"] = {"high_c": 1.0}
    path = tmp_path / "location.yaml"
    path.write_text(yaml.safe_dump(raw))

    got = deviation_bands(load_location_config(path))
    assert got.high_c == 1.0
    assert got.onset_min == DeviationBands().onset_min
    assert got.low_c == DeviationBands().low_c


def test_a_mapping_entry_loads_beside_a_bare_string(tmp_path):
    """Mixed forms in one list — ROADMAP item 81, 2026-09-22.

    The chain's first link is the incumbent named the way it always was; the
    ones after it name their own credentials. Both have to load from the same
    YAML list or the change would force every config file to be rewritten.
    """
    from openlocalweather.config import LLMProviderEntry, load_location_config

    # Set on the PARSED live config rather than by editing its text: the live
    # list stopped starting with a bare "- gemini" at item 186, and a text
    # replace that matches nothing silently tests the live file instead.
    import yaml

    data = yaml.safe_load(Path("config/location.yaml").read_text())
    data["location"]["llm_providers"] = [
        "gemini",
        {"kind": "openai", "name": "openrouter", "env_prefix": "OPENROUTER", "fallback_models": ["a:free"]},
    ]
    path = tmp_path / "location.yaml"
    path.write_text(yaml.safe_dump(data))

    cfg = load_location_config(str(path))
    first, second = cfg.llm_providers[0], cfg.llm_providers[1]

    assert first == "gemini"
    assert isinstance(second, LLMProviderEntry)
    assert (second.kind, second.name, second.env_prefix) == (
        "openai", "openrouter", "OPENROUTER",
    )
    assert second.fallback_models == ["a:free"]


def test_a_mapping_entry_with_an_unknown_kind_is_rejected_at_load():
    """Same guard the bare strings have had, on the other form."""
    import pytest
    from pydantic import ValidationError

    from openlocalweather.config import LLMProviderEntry

    with pytest.raises(ValidationError, match="unknown llm_providers kind"):
        LLMProviderEntry(kind="opeanai")


# ---------------------------------------------------------------------------
# The queue link — ROADMAP item 186
# ---------------------------------------------------------------------------


def test_a_queue_link_is_accepted():
    from openlocalweather.config import LLMProviderEntry

    entry = LLMProviderEntry(
        kind="gemini-interactions", name="gemini-queue", background=True, max_attempts=1,
        poll_delays_s=[480, 480], write_up_poll_delays_s=[480, 480, 840, 1800],
        health_check_poll_delays_s=[1800, 1800], max_calls_per_run=5,
    )
    assert entry.poll_delays_s == [480, 480]
    assert entry.write_up_poll_delays_s == [480, 480, 840, 1800]
    assert entry.health_check_poll_delays_s == [1800, 1800]
    assert entry.max_calls_per_run == 5


@pytest.mark.parametrize("fields, why", [
    # A schedule nothing would use: without `background` the endpoint answers
    # directly and no poll is ever sent.
    ({"kind": "gemini-interactions", "poll_delays_s": [480]}, "background"),
    ({"kind": "gemini-interactions", "write_up_poll_delays_s": [480]}, "background"),
    ({"kind": "gemini-interactions", "health_check_poll_delays_s": [1800]}, "background"),
    # Only the Interactions endpoint queues.
    ({"kind": "gemini", "background": True}, "gemini-interactions"),
    ({"kind": "openai", "poll_delays_s": [480]}, "gemini-interactions"),
    # Gemini's schedule is four attempts long; more would outrun its delays and
    # the budget guard that assumes them. OpenRouter retries on its own terms.
    ({"kind": "gemini", "max_attempts": 0}, "max_attempts"),
    ({"kind": "gemini", "max_attempts": 5}, "max_attempts"),
    ({"kind": "openai", "max_attempts": 1}, "max_attempts"),
    ({"kind": "gemini-interactions", "background": True, "poll_delays_s": []}, "poll"),
    ({"kind": "gemini-interactions", "background": True, "poll_delays_s": [0]}, "poll"),
    ({"kind": "gemini", "max_calls_per_run": 0}, "max_calls_per_run"),
])
def test_a_queue_setting_that_would_do_nothing_is_rejected(fields, why):
    """A setting that is silently ignored makes a run look configured and
    behave otherwise, so each is refused at load, naming the field."""
    from pydantic import ValidationError

    from openlocalweather.config import LLMProviderEntry

    with pytest.raises(ValidationError, match=why):
        LLMProviderEntry(**fields)


# ---------------------------------------------------------------------------
# One run a day — the operator's decision, 2026-09-30
# ---------------------------------------------------------------------------


def test_a_gemini_link_may_space_its_retries():
    from openlocalweather.config import LLMProviderEntry

    entry = LLMProviderEntry(kind="gemini", max_attempts=3, retry_delays_s=[180, 420])

    assert entry.retry_delays_s == [180, 420]


@pytest.mark.parametrize("fields, why", [
    ({"kind": "openai", "retry_delays_s": [180]}, "retry_delays_s"),
    # Each attempt after the first waits one gap, so the gaps bound the attempts.
    ({"kind": "gemini", "max_attempts": 3, "retry_delays_s": [180]}, "retry_delays_s"),
    # Unset, max_attempts is the default schedule's four: the third retry
    # would look for a gap that is not there, mid-run.
    ({"kind": "gemini", "retry_delays_s": [180, 420]}, "retry_delays_s"),
    ({"kind": "gemini", "retry_delays_s": []}, "retry_delays_s"),
    ({"kind": "gemini", "retry_delays_s": [-1]}, "retry_delays_s"),
])
def test_a_retry_schedule_that_cannot_hold_is_rejected(fields, why):
    from pydantic import ValidationError

    from openlocalweather.config import LLMProviderEntry

    with pytest.raises(ValidationError, match=why):
        LLMProviderEntry(**fields)


def test_the_write_up_may_wait_after_the_scored_call():
    from openlocalweather.config import load_location_config

    assert load_location_config("config/location.yaml").llm_write_up_delay_s == 120


def test_the_live_config_spends_under_ten_gemini_calls_a_day():
    """The operator's target, 2026-09-30: one reliable forecast a day for
    fewer than 10 Gemini calls. A run makes two calls (the scored call and
    the write-up), each up to `max_attempts`; the write-up's second chance
    makes one more call on the same link. Derived from the live config so
    a change to either fails here."""
    from openlocalweather.config import load_location_config
    from openlocalweather.spend import LLM_CALLS_PER_FORECAST

    location = load_location_config("config/location.yaml")
    gemini = [e for e in location.llm_providers if not isinstance(e, str) and e.kind == "gemini"]
    [direct] = gemini
    second_chance_calls = 1

    worst_case = (LLM_CALLS_PER_FORECAST + second_chance_calls) * direct.max_attempts

    assert worst_case < 10
    assert not any(not isinstance(e, str) and e.kind == "gemini-interactions" for e in location.llm_providers), (
        "the queue left the chain on 2026-09-30"
    )
