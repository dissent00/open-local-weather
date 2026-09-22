"""Ask each candidate model to write a real narrative, and check its SHAPE.

ROADMAP item 81's supported matrix, in the smallest form that answers a
question we actually had. On 2026-09-22 the chain fell back for the first
time and `nvidia/nemotron-3-super-120b-a12b:free` produced a narrative with
every newline turned into a bare letter `n` — six `n##` heading joins — and
exactly 4096 characters ending mid-sentence. The operator asked the obvious
next question: does that rule out free models, or just that one?

Guessing from parameter counts and capability flags is what put that model
first. This asks the models instead.

WHAT IT CHECKS IS SHAPE, NOT QUALITY. Whether a forecast is any good needs a
reader; whether it is structurally publishable does not, and the failure that
shipped was structural. A model that cannot put a newline in a JSON string
cannot write this document however well it reasons.

Runs through `OpenAiCompatProvider` — the same class, schema and
`require_parameters` production uses — so a pass here is a pass on the real
path rather than on a simplified one.

Usage (the key comes from the environment, never an argument):
    LLM_API_KEY=... python tools/probe_models.py <date> <model> [<model> ...]
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather.config import load_location_config
from openlocalweather.llm.openai_compat import OpenAICompatProvider
from openlocalweather.llm.prompt import build_narrative_prompt, build_narrative_user_prompt
from openlocalweather.llm.schema import GeminiNarrativeResponse

BASE_URL = "https://openrouter.ai/api/v1"

#: A heading must start a line. Markdown that never does is not publishable,
#: whatever it says.
HEADING = re.compile(r"^## ", re.M)
#: The signature of the 09-22 failure: a heading welded to the previous word
#: by the letter that used to be an escape.
WELDED_HEADING = re.compile(r"[a-z]n##")


def verdict(text: str) -> list[str]:
    faults = []
    if not text.strip():
        return ["EMPTY"]
    if WELDED_HEADING.search(text):
        faults.append("newlines came back as the letter n")
    if not HEADING.search(text):
        faults.append("no heading starts a line")
    if text.count("\n") == 0:
        faults.append("no newlines at all")
    if not text.rstrip().endswith((".", "!", "?", "’", '"')):
        faults.append(f"ends mid-sentence: ...{text.rstrip()[-40:]!r}")
    return faults


def main() -> int:
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("LLM_API_KEY (or OPENROUTER_API_KEY) is required.", file=sys.stderr)
        return 2

    day, models = sys.argv[1], sys.argv[2:]
    archived = json.loads((ROOT / f"data/prompts/{day}.json").read_text())
    entry = json.loads((ROOT / f"data/log/{day}.json").read_text())
    cfg = load_location_config(str(ROOT / "config/location.yaml"))

    system = build_narrative_prompt(
        cfg, verification_already_written=True, ground_stations_configured=True,
        local_bulletin_configured=True, extended_outlook_available=True)
    rows = (entry.get("prediction_rows") or [{}])[0].get("predictions") or {}
    extended = []
    for lead, k in ((3, "day3"), (7, "day7")):
        blend = next((r for r in rows.get(k, []) if r.get("model") == "olw_blend"), None)
        if blend:
            extended.append({"lead_time_days": lead, "rain": blend.get("rain"),
                             "rain_probability_pct": blend.get("rain_probability_pct")})
    user = build_narrative_user_prompt(
        archived["issuances"][-1]["user_prompt"],
        {"today_properties": entry.get("today_properties") or {},
         "extended_properties": extended},
    )
    print(f"prompt pair from {day}: system {len(system):,} chars, user {len(user):,}\n")

    worst = 0
    for model in models:
        provider = OpenAICompatProvider(
            api_key=key, model=model, base_url=BASE_URL,
            json_mode="json_schema", require_parameters=True,
        )
        try:
            got = provider.generate(system, user, GeminiNarrativeResponse)
        except Exception as e:  # noqa: BLE001 — every failure is a result here
            print(f"  {model}\n      REFUSED  {type(e).__name__}: {str(e)[:160]}\n")
            worst = max(worst, 1)
            continue

        text = got.today_narrative or ""
        faults = verdict(text)
        print(f"  {model}")
        print(f"      {len(text):>6,} chars, {text.count(chr(10))} newlines, "
              f"{len(HEADING.findall(text))} headings")
        if faults:
            worst = max(worst, 1)
            for f in faults:
                print(f"      FAULT  {f}")
        else:
            print("      shape OK")
        print()
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
