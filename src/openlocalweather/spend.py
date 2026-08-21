"""A hard cap on LLM calls, enforced before any call is made.

WHY THIS IS A CORRECTNESS REQUIREMENT, NOT A SETTING.

Nothing in this project counted API calls until now. The only limiter was
daily.yml's `already_done` skip check, which `force: true` deliberately
bypasses — so a mistake in a crontab on a machine nobody is watching could
run up an unbounded bill against someone's key, and the first signal would
be the bill. A fork running this on their own key is exposed the same way,
and the app spends the user's own money through a key they supplied.

HOW THIS FITS "RECOMPUTE, NEVER ACCUMULATE".

It fits exactly, despite first appearances. That principle forbids storing
*derived* numbers — accuracy percentages, all-time counts — because a wrong
one persists invisibly. It has never forbidden storing raw records: the
whole of `data/log/` is append-only and never recomputed.

A spend ledger is raw data of the same kind. Each entry records one call
attempt; the number of calls in the last 24 hours is **recomputed from those
entries every time** and never stored. So the derived figure stays derived,
exactly as everywhere else.

WHAT MAKES IT IRONCLAD RATHER THAN MERELY PRESENT.

- **Calls, not forecasts.** One forecast can cost up to MAX_ATTEMPTS calls
  when a provider is flaky. A cap on successful runs is not a cap on spend.
- **Recorded BEFORE the call, never after.** A crash, timeout or kill
  between sending and recording would otherwise lose the count and silently
  permit an overrun — precisely when things are already going wrong. A call
  attempt is also unrecoverable: unlike weather actuals, it cannot be
  re-derived after the fact, so the moment is the only chance to record it.
- **Fails closed.** If the ledger cannot be read or written, no call is
  made. A guard that degrades to "allow" is not a guard.
- **Rolling 24 hours, not a calendar day.** A midnight reset would permit a
  full budget on either side of it, so a cap of 10 would allow 20 in a few
  hours.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Deliberately above the honest worst case rather than at it. The pipeline
# nominally makes two calls a day (morning plus evening refresh) and up to
# eight if every attempt retries to the limit. Ten leaves headroom for a bad
# day while making a runaway loop impossible.
#
# A cap only protects operators who have one, so this default is NOT
# unlimited — an unset cap protects nobody.
DEFAULT_MAX_LLM_CALLS_PER_24H = 10

WINDOW = timedelta(hours=24)


class SpendCapExceeded(RuntimeError):
    """Raised instead of making a call that would exceed the cap.

    Loud on purpose. Skipping quietly would mean discovering the cap was set
    too low only by noticing missing forecasts days later — the same
    "tolerance without vigilance" failure this project has already been bitten
    by, where the system survives a problem and never mentions it.
    """


@dataclass(frozen=True)
class SpendRecord:
    """One call attempt. Written before the attempt, never edited after."""

    at: datetime
    provider: str
    model: str
    # What the call was for, so a ledger read later can tell a forecast from
    # a health check without cross-referencing timestamps.
    purpose: str

    def to_json(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "provider": self.provider,
            "model": self.model,
            "purpose": self.purpose,
        }

    @staticmethod
    def from_json(d: dict) -> SpendRecord:
        return SpendRecord(
            at=datetime.fromisoformat(d["at"]),
            provider=d.get("provider", ""),
            model=d.get("model", ""),
            purpose=d.get("purpose", ""),
        )


def ledger_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "spend_ledger.json"


def read_ledger(data_dir: str | Path) -> list[SpendRecord]:
    """Every recorded attempt.

    A malformed ledger raises rather than returning empty. Everywhere else in
    this project an unreadable file degrades gracefully, because losing some
    history is better than refusing to run. Here the opposite holds: an
    unreadable ledger that reads as "no calls yet" would silently disable the
    cap at the exact moment something is already wrong.
    """
    path = ledger_path(data_dir)
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    entries = raw.get("calls", []) if isinstance(raw, dict) else raw
    return [SpendRecord.from_json(e) for e in entries]


def _write_ledger(data_dir: str | Path, records: list[SpendRecord]) -> None:
    path = ledger_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "note": (
                    "Append-only record of LLM call attempts. Written BEFORE "
                    "each call so a crash cannot lose the count. The 24-hour "
                    "total is recomputed from these entries, never stored."
                ),
                "calls": [r.to_json() for r in records],
            },
            indent=2,
        )
    )


def calls_in_window(
    records: list[SpendRecord], now: datetime, window: timedelta = WINDOW
) -> int:
    """Recomputed on every check — never stored, like every other statistic."""
    cutoff = now - window
    return sum(1 for r in records if r.at > cutoff)


def prune(
    records: list[SpendRecord], now: datetime, keep: timedelta = timedelta(days=7)
) -> list[SpendRecord]:
    """Drops entries far older than the window.

    Keeps a week rather than exactly 24 hours: the extra history costs
    nothing and makes "what did it spend last Tuesday" answerable, which
    matters the first time a bill looks wrong.
    """
    cutoff = now - keep
    return [r for r in records if r.at > cutoff]


def assert_capacity(
    data_dir: str | Path,
    *,
    max_calls: int = DEFAULT_MAX_LLM_CALLS_PER_24H,
    now: datetime | None = None,
) -> None:
    """Raises if the cap is already reached, WITHOUT recording anything.

    Belt and braces, and deliberately not redundant. Enforcement proper lives
    in the per-request hook the providers call, but a provider is an injected
    dependency: a custom or stubbed one that ignores the hook would sail past
    the cap entirely, and a guard that can be bypassed by supplying the wrong
    object is not a guard. This check runs on the pipeline's own path, where
    nothing can opt out of it.

    It cannot replace the hook — it has no idea how many requests a single
    generate() will end up sending — so the two do different jobs: this one
    refuses to START an over-budget run, the hook refuses to CONTINUE one.
    """
    now = now or datetime.now(timezone.utc)
    records = read_ledger(data_dir)
    used = calls_in_window(records, now)
    if used >= max_calls:
        oldest_in_window = min(
            (r.at for r in records if r.at > now - WINDOW), default=now
        )
        raise SpendCapExceeded(
            f"LLM call refused before starting: {used} of {max_calls} allowed "
            f"calls already made in the last 24 hours. The oldest ages out at "
            f"{(oldest_in_window + WINDOW).isoformat()}. Raise "
            f"max_llm_calls_per_24h in config/location.yaml if it is set too "
            f"low for this deployment."
        )


def record_attempt(
    data_dir: str | Path,
    *,
    provider: str,
    model: str,
    purpose: str,
    max_calls: int = DEFAULT_MAX_LLM_CALLS_PER_24H,
    now: datetime | None = None,
) -> int:
    """Reserves one call, or raises [SpendCapExceeded].

    Call this IMMEDIATELY BEFORE making a request, never after. The write
    happens first precisely so that a process killed mid-call still leaves
    the attempt counted: over-counting refuses a call that would have been
    allowed, which is the safe direction to be wrong in.

    Returns the number of calls now used in the window, for logging.
    """
    now = now or datetime.now(timezone.utc)
    records = read_ledger(data_dir)
    used = calls_in_window(records, now)

    if used >= max_calls:
        oldest_in_window = min(
            (r.at for r in records if r.at > now - WINDOW), default=now
        )
        frees_at = oldest_in_window + WINDOW
        raise SpendCapExceeded(
            f"LLM call refused: {used} of {max_calls} allowed calls already made "
            f"in the last 24 hours. The oldest of those ages out at "
            f"{frees_at.isoformat()}. This is a hard cap, not a rate limit — "
            f"raise max_llm_calls_per_24h in config/location.yaml if it is set "
            f"too low for this deployment."
        )

    records = prune(records, now)
    records.append(SpendRecord(at=now, provider=provider, model=model, purpose=purpose))
    _write_ledger(data_dir, records)
    return used + 1
