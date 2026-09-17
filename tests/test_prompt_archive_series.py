"""ROADMAP item 148, step 2: the trailing series the growth check reads."""

from datetime import date, datetime, timezone

from openlocalweather.store.prompt_archive import first_issuance_prompts, write_prompt_archive


def _archive(tmp_path, d: date, *prompts: str) -> None:
    for i, prompt in enumerate(prompts):
        write_prompt_archive(
            tmp_path, d,
            issued_at=datetime(d.year, d.month, d.day, 3 + 12 * i, tzinfo=timezone.utc),
            judgment_prompt="j", narrative_prompt="n", user_prompt=prompt, llm_model="t",
        )


def test_only_the_first_issuance_of_each_day_and_only_days_before(tmp_path):
    """A later issuance is smaller by design (measured −4,945 on 2026-09-16),
    so it is a different series; and today's own archive, if any, is not
    part of what today is compared against."""
    _archive(tmp_path, date(2026, 9, 10), "a" * 10)
    _archive(tmp_path, date(2026, 9, 11), "b" * 20, "later" * 2)
    _archive(tmp_path, date(2026, 9, 12), "c" * 30)
    _archive(tmp_path, date(2026, 9, 13), "today" * 3)

    got = first_issuance_prompts(tmp_path, before=date(2026, 9, 13), limit=7)
    assert got == ["a" * 10, "b" * 20, "c" * 30]


def test_the_limit_keeps_the_newest(tmp_path):
    for i in range(1, 6):
        _archive(tmp_path, date(2026, 9, i), str(i) * i)
    assert first_issuance_prompts(tmp_path, before=date(2026, 9, 6), limit=2) == ["4444", "55555"]


def test_no_archive_is_an_empty_series(tmp_path):
    assert first_issuance_prompts(tmp_path, before=date(2026, 9, 6), limit=7) == []
