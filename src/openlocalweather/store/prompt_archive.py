"""Read/write the inputs a forecast was built from: data/prompts/YYYY-MM-DD.json.

ROADMAP item 69. Until this existed the pipeline stored every OUTPUT of a run
and none of its INPUTS — `guidance_source` and `guidance_age_hours` were kept,
the guidance itself was not — so a day's forecast could be read back but never
reproduced.

The consequence was not obvious and was expensive: **a model cannot be
evaluated against a day whose inputs are gone.** That single fact forced every
model question into a forward experiment (run the candidate live for a month,
pay a call a day) which is slow and confounds the model with the weather, since
days differ enormously in how predictable they are. With the inputs on disk the
same question is a paired backtest — same bytes in, same recorded outcome to
score against — answered in an afternoon, for any candidate, including one that
does not exist yet.

It also removes model retirement as a category of problem. You never needed the
dead model; you needed its inputs and the outcomes, and both are now kept.

WHAT IS STORED, AND WHY NOT MORE

The rendered user prompt, verbatim. Not a recipe for rebuilding it: a recipe
re-derives through code that will have changed by the time anyone replays it,
which reintroduces exactly the drift this exists to avoid.

The system prompt is stored as a SHA-256 only. It is ~34 KB against the user
prompt's ~4.6 KB, it is vector-pinned in spec/vectors/llm_system_prompt.json,
and it is recoverable from git — but only if you know WHICH one was used, which
is what the hash answers and what nothing recorded before item 70.

One file per date, matching log_store's convention and for the same reason:
each day's commit touches one file, so diffs stay reviewable and history never
gets rewritten. Within a file, issuances accumulate in order — a morning run
and an evening refresh are separate entries, because they are separate
forecasts built from different inputs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from openlocalweather.dates import format_date, parse_date

# The archive is append-only within a day and keyed by issuance time, so a
# re-run cannot silently overwrite the inputs of the run it replaces.
_ISSUED_AT = "issued_at"


def prompt_sha256(prompt: str) -> str:
    """Identity of a rendered prompt, for ROADMAP item 70.

    The forecaster is (model + prompt + input set), and until this existed only
    the model was recorded — `meta.pipeline_version` was the string "0.1.0" and
    had never changed, while the prompt was edited twice on 2026-09-04 alone.
    Prompt changes are far more frequent than model changes and at least as
    capable of moving the output, so a record that partitions on the model
    alone partitions on the slower axis.

    Hex rather than truncated: a prefix collision would silently merge two
    forecasters in the record, and the cost of the full digest is 64 bytes.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def prompt_archive_path(data_dir: str | Path, d: date) -> Path:
    return Path(data_dir) / "prompts" / f"{format_date(d)}.json"


def read_prompt_archive(data_dir: str | Path, d: date) -> list[dict]:
    """Every issuance archived for that date, oldest first.

    Returns [] for a date with no archive, which is the normal condition for
    every day before this shipped as well as for days that never ran. Callers
    that need to distinguish "no archive" from "archive is empty" should check
    the path; nothing does yet, and inventing a three-valued return for a
    hypothetical caller would be the more expensive mistake.
    """
    path = prompt_archive_path(data_dir, d)
    if not path.exists():
        return []

    return json.loads(path.read_text(encoding="utf-8"))["issuances"]


def write_prompt_archive(
    data_dir: str | Path,
    d: date,
    *,
    issued_at: datetime,
    system_prompt: str,
    user_prompt: str,
    llm_model: str,
) -> Path:
    """Appends one issuance's inputs to the date's archive.

    Idempotent on `issued_at`: re-archiving the same instant replaces that
    entry rather than duplicating it, so a retried write cannot leave the same
    forecast in the record twice under two identical timestamps.
    """
    path = prompt_archive_path(data_dir, d)
    path.parent.mkdir(parents=True, exist_ok=True)

    stamp = issued_at.isoformat()
    issuances = [i for i in read_prompt_archive(data_dir, d) if i[_ISSUED_AT] != stamp]
    issuances.append(
        {
            _ISSUED_AT: stamp,
            "llm_model": llm_model,
            "system_prompt_sha256": prompt_sha256(system_prompt),
            "user_prompt": user_prompt,
        }
    )
    issuances.sort(key=lambda i: i[_ISSUED_AT])

    payload = {
        "date": format_date(d),
        "note": (
            "Inputs a forecast was built from — ROADMAP item 69. The user "
            "prompt verbatim; the system prompt by hash, recoverable from git "
            "and from spec/vectors/llm_system_prompt.json."
        ),
        "issuances": issuances,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return path


def list_archived_dates(data_dir: str | Path) -> list[date]:
    """Every date with an archive, oldest first. The backtestable set."""
    directory = Path(data_dir) / "prompts"
    if not directory.exists():
        return []

    return sorted(parse_date(p.stem) for p in directory.glob("*.json"))
