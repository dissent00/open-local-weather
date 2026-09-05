"""The inputs a forecast was built from — ROADMAP items 69 and 70.

Driven through the REAL pipeline rather than by calling the store directly,
because the failure this guards against is not "the writer is broken". It is
"the writer is never called", or "it is called with the morning's prompt on
the evening run" — neither of which a unit test of the store can see.
"""

from datetime import date, datetime, timezone

import pytest

from openlocalweather.store import log_store, prompt_archive
from openlocalweather.store.prompt_archive import prompt_sha256

# patch_fetches is autouse, but only in the module that DEFINES it — an
# autouse fixture does not reach a module that merely imports its neighbour's
# helpers. Without it the three tests here that drive the real pipeline sent
# live requests to api.open-meteo.com, including the backstop that is supposed
# to make exactly that impossible. Importing the fixture binds it into this
# module's namespace, which is what makes it apply.
from tests.test_pipeline_run import make_deps, patch_fetches  # noqa: F401
from openlocalweather.pipeline import run_daily_pipeline

TODAY = date(2026, 8, 11)


def test_a_real_run_archives_the_prompt_it_actually_sent(tmp_path):
    run_daily_pipeline(make_deps(tmp_path), today=TODAY, dry_run=False)

    issuances = prompt_archive.read_prompt_archive(tmp_path, TODAY)
    assert len(issuances) == 1

    archived = issuances[0]
    entry = log_store.read_log_entry(tmp_path, TODAY)

    # The point of the archive is that it is REPLAYABLE, which means the
    # stored prompt has to be the RENDERED one — this day's dates and this
    # day's guidance — not a template with placeholders still in it.
    assert "Today's Date: 2026-08-11" in archived["user_prompt"]
    assert "{" not in archived["user_prompt"].split("\n")[1]
    assert archived["llm_model"] == entry.meta.llm_model

    # And that the entry can be tied back to its own inputs without guessing
    # at deploy timing, which is the whole of item 70.
    assert archived["system_prompt_sha256"] == entry.meta.system_prompt_sha256
    assert len(entry.meta.system_prompt_sha256) == 64


def test_a_dry_run_archives_nothing(tmp_path):
    """A dry run must leave no trace, and the archive is a new way to leave
    one. Grouped with the existing dry-run assertions in spirit: the reason
    this file exists at all is that a new write path is easy to add and easy
    to forget to gate."""
    run_daily_pipeline(make_deps(tmp_path), today=TODAY, dry_run=True)

    assert prompt_archive.read_prompt_archive(tmp_path, TODAY) == []
    assert not (tmp_path / "prompts").exists()


def test_a_second_issuance_is_added_not_overwritten(tmp_path):
    """The morning run and the evening refresh are different forecasts from
    different inputs. Keeping only the last would make the day's first
    issuance permanently un-backtestable while the log still shows it
    happened — the archive claiming completeness it does not have."""
    data_dir = tmp_path
    morning = datetime(2026, 8, 11, 3, 4, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 11, 15, 2, tzinfo=timezone.utc)

    for issued_at, user_prompt in ((morning, "morning inputs"), (evening, "evening inputs")):
        prompt_archive.write_prompt_archive(
            data_dir,
            TODAY,
            issued_at=issued_at,
            system_prompt="system",
            user_prompt=user_prompt,
            llm_model="m",
        )

    issuances = prompt_archive.read_prompt_archive(data_dir, TODAY)
    assert [i["user_prompt"] for i in issuances] == ["morning inputs", "evening inputs"]


def test_re_archiving_one_instant_replaces_rather_than_duplicates(tmp_path):
    """A retried write must not leave the same forecast in the record twice.
    Two entries under one timestamp would inflate any count taken over the
    archive, and there is no way to tell afterwards which was the real one."""
    issued_at = datetime(2026, 8, 11, 3, 4, tzinfo=timezone.utc)

    for user_prompt in ("first attempt", "same run, written again"):
        prompt_archive.write_prompt_archive(
            tmp_path,
            TODAY,
            issued_at=issued_at,
            system_prompt="system",
            user_prompt=user_prompt,
            llm_model="m",
        )

    issuances = prompt_archive.read_prompt_archive(tmp_path, TODAY)
    assert len(issuances) == 1
    assert issuances[0]["user_prompt"] == "same run, written again"


def test_the_hash_distinguishes_prompts_that_differ_by_one_rule(tmp_path):
    """Item 70's whole purpose. Item 67's rewrite changed what the opening
    sentence of every evening forecast is allowed to contain, and under the
    old record that entry was indistinguishable from the ones before it."""
    before = "Anything already past is past tense, or left out."
    after = "LEAVE A SPENT VALUE OUT unless it changes what the reader should DO."

    assert prompt_sha256(before) != prompt_sha256(after)
    assert prompt_sha256(before) == prompt_sha256(before)


def test_a_date_with_no_archive_reads_as_empty_not_an_error(tmp_path):
    """Every day before this shipped is such a date, and so is every day that
    never ran. Raising here would make the backtest harness's first act be
    catching an exception for the normal case."""
    assert prompt_archive.read_prompt_archive(tmp_path, TODAY) == []
    assert prompt_archive.list_archived_dates(tmp_path) == []


def test_the_archived_set_is_the_backtestable_set(tmp_path):
    for d in (date(2026, 8, 11), date(2026, 8, 13), date(2026, 8, 12)):
        prompt_archive.write_prompt_archive(
            tmp_path,
            d,
            issued_at=datetime(d.year, d.month, d.day, 3, tzinfo=timezone.utc),
            system_prompt="system",
            user_prompt="inputs",
            llm_model="m",
        )

    assert prompt_archive.list_archived_dates(tmp_path) == [
        date(2026, 8, 11),
        date(2026, 8, 12),
        date(2026, 8, 13),
    ]


def test_an_entry_written_before_the_field_existed_says_so(tmp_path):
    """Three-valued, like degradations. None means "never recorded", and its
    prompt is genuinely unrecoverable — a default of "" would claim an
    identity those runs never had, and the backtest harness would then try to
    resolve a hash that matches nothing."""
    from openlocalweather.models import LogEntryMeta

    meta = LogEntryMeta(
        generated_at_utc=datetime(2026, 8, 11, tzinfo=timezone.utc),
        llm_provider="GeminiProvider",
        llm_model="gemini-3.6-flash",
        pipeline_version="0.1.0",
    )
    assert meta.system_prompt_sha256 is None


def test_the_evening_refresh_archives_its_own_prompt_not_the_mornings(tmp_path):
    """The bug a store-level test cannot see.

    Both paths build a `system_prompt` and a `user_prompt` local, and the
    refresh's differ from the morning's — `is_reissue=True` alone makes them
    different documents. Archiving the wrong local would produce an archive
    that looks complete, replays cleanly, and answers about the wrong run.
    """
    from openlocalweather import pipeline
    from tests.test_pipeline_run import FakeLLMProvider

    run_daily_pipeline(make_deps(tmp_path), today=TODAY, dry_run=False)

    llm = FakeLLMProvider()
    pipeline.run_refresh_pipeline(make_deps(tmp_path, llm=llm), today=TODAY, dry_run=False)

    issuances = prompt_archive.read_prompt_archive(tmp_path, TODAY)
    assert len(issuances) == 2, "the morning issuance must survive the refresh"

    sent_system, sent_user = llm.calls[-1]
    assert issuances[-1]["user_prompt"] == sent_user
    assert issuances[-1]["system_prompt_sha256"] == prompt_sha256(sent_system)

    # The two issuances are genuinely different forecasters, and the record
    # now says so rather than carrying the morning's identity forward.
    assert issuances[0]["system_prompt_sha256"] != issuances[-1]["system_prompt_sha256"]

    entry = log_store.read_log_entry(tmp_path, TODAY)
    assert entry.meta.system_prompt_sha256 == issuances[-1]["system_prompt_sha256"]
