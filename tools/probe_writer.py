"""The writer backtest — ROADMAP item 191 step (d).

    GROQ_API_KEY=... python tools/probe_writer.py --kind openai --env-prefix GROQ \\
        --base-url https://api.groq.com/openai/v1 --model openai/gpt-oss-120b [--days 8] [--out probe.md]

One route, asked the request production sends (`writer_ask`: the brief
and the writer prompt for each archived day with a served call, newest
first), one try per day, and each answered section audited as `olw
write-up` audits it. Prints per day what came back and why a section was
refused, then per section the pass rate; writes every text to `--out` so
the operator can read three beside a stored narrative. Go per route at
80% of sections passing (the roadmap's bar); a refusal rate is a finding
of its own, as it is for `probe_models.py`.

Runs on the real path — `_build_llm_provider` from the same entry shape
location.yaml takes — so what passes here passes in production; and
COUNTED on the ledger under `probe`, like every caller that reaches a
model. The key comes from the environment, never an argument.
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather.cli import _build_llm_provider  # noqa: E402
from openlocalweather.config import load_location_config  # noqa: E402
from openlocalweather.llm.schema import WriteUpResponse  # noqa: E402
from openlocalweather.pipeline import attach_spend_cap  # noqa: E402
from openlocalweather.store.log_store import read_log_entry  # noqa: E402
from openlocalweather.writer import audit_section, brief_inputs, writer_ask  # noqa: E402

PASS_BAR_PCT = 80


def archived_days(data_dir: Path, limit: int) -> list[date]:
    """The newest archived days whose stored entry holds a served call."""
    days = []
    for path in sorted(data_dir.glob("prompts/*.json"), reverse=True):
        day = date.fromisoformat(path.stem)
        entry = read_log_entry(data_dir, day)
        if entry is not None and entry.served_call:
            days.append(day)
        if len(days) == limit:
            break
    return days


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", default="openai")
    parser.add_argument("--env-prefix", default="LLM")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--config", default=str(ROOT / "config/location.yaml"))
    parser.add_argument("--out", default=None, help="Write every answered section here, as Markdown.")
    args = parser.parse_args()

    location = load_location_config(args.config)
    data_dir = Path(args.data_dir)
    entry = {"kind": args.kind, "name": "probe", "env_prefix": args.env_prefix, "model": args.model,
             "max_attempts": 1}
    if args.base_url:
        entry["base_url"] = args.base_url
    provider = _build_llm_provider(providers=[entry])
    verify_spend, _ = attach_spend_cap(
        provider, data_dir, max_calls=location.max_llm_calls_per_24h, purpose="probe"
    )

    days = archived_days(data_dir, args.days)
    print(f"{args.model} via {args.env_prefix}: {len(days)} archived days, one request each\n")
    asked = passed = refused_days = 0
    by_section: dict[str, list[int]] = {}
    texts: list[str] = []
    for day in days:
        stored = read_log_entry(data_dir, day)
        archive = json.loads((data_dir / "prompts" / f"{day}.json").read_text())
        inputs = brief_inputs(location, stored, archive["issuances"][-1]["user_prompt"])
        ask = writer_ask(location, inputs)
        if ask is None:
            print(f"  {day}: nothing to ask")
            continue
        started = time.monotonic()
        try:
            answer = provider.generate(ask.system_prompt, ask.brief, WriteUpResponse)
        except Exception as e:  # noqa: BLE001 — every failure is a result here
            refused_days += 1
            print(f"  {day}: REFUSED after {time.monotonic() - started:.0f} s  {type(e).__name__}: {str(e)[:160]}")
            continue
        elapsed = time.monotonic() - started
        print(f"  {day}: answered in {elapsed:.0f} s, {len(ask.brief):,} characters of brief")
        for section in ask.sections:
            text = (getattr(answer, section) or "").strip()
            tally = by_section.setdefault(section, [0, 0])
            tally[1] += 1
            asked += 1
            if not text:
                print(f"      {section:<10} unanswered")
                continue
            defects = audit_section(section, text, ask.brief, inputs)
            words = len(text.split())
            if defects:
                print(f"      {section:<10} refused: {defects[0]}  ({words} words)")
            else:
                tally[0] += 1
                passed += 1
                print(f"      {section:<10} passed  ({words} words)")
            texts.append(f"## {day} — {section}" + (f" — REFUSED: {defects[0]}" if defects else "") + f"\n\n{text}\n")
    verify_spend()

    print()
    for section, (ok, n) in by_section.items():
        print(f"  {section:<10} {ok}/{n} passed")
    if asked:
        rate = 100 * passed // asked
        print(f"\n  {passed}/{asked} sections passed ({rate}%), {refused_days} of {len(days)} days refused; "
              f"the bar is {PASS_BAR_PCT}%: {'GO' if rate >= PASS_BAR_PCT else 'NO GO'}")
    if args.out:
        Path(args.out).write_text("\n".join(texts))
        print(f"  texts written to {args.out}")
    return 0 if asked and 100 * passed // asked >= PASS_BAR_PCT else 1


if __name__ == "__main__":
    raise SystemExit(main())
