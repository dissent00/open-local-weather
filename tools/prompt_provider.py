"""The prompt-provider model's files — ROADMAP item 182.

The operator publishes each location's DATA at a URL; a reader runs it in
their own chat app, with the instructions saved once in a Claude Project or a
custom GPT. This tool writes what gets published.

`probe` comes first because every design here is sized by one number nobody
has: how much fetched text a chat app actually holds. Free ChatGPT's pricing
page, read 2026-09-24, gives only "27K" total context and "~12 pages of text"
of input for Instant, and "Varies" for its reasoning model. Claude chat was
measured cutting the same file near 119,700 characters. A package sized from a
page count would be a threshold set from an estimate, which is what item 100
of this roadmap records going wrong.

THE PROBE IS REAL PROMPT TEXT, not filler. Tokens per character depend on the
content — this prompt is dense with numbers, tabs and repeated keys — so a
ceiling measured on lorem ipsum would not transfer.

CHECKPOINT CODE WORDS ARE WHAT MAKE IT A TEST OF THE CONTEXT, not of the
fetch. ChatGPT reported the 169,138-character archive's length exactly, but it
counted with its code tool, which proves the bytes arrived and not that the
model could read them. A code word cannot be counted or guessed, only seen, so
the last one a model can quote is the last text it holds.

`lite` writes the small forecaster's data — route B of item 182, chosen by
the operator 2026-09-24 so that a FREE ChatGPT account can run it. `score`
checks a set of replies against it and scores their calls beside the full
forecaster's and every model's on the same days.

Usage:
  python tools/prompt_provider.py probe <date> <out_file> [chars] [every]
  python tools/prompt_provider.py lite <date> <out_file> [issuance_index]
  python tools/prompt_provider.py score <replies_dir>
"""
import hashlib
import json
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openlocalweather.llm.prompt_size import prompt_block_sizes  # noqa: E402

PROBE_CHARS = 120_000
CHECKPOINT_EVERY = 4_000
MARKER_HASH_CHARS = 12

# Words that cannot occur in a weather prompt, so a quoted one was seen and
# not pattern-matched. Checked against the source text on every run.
CODE_WORDS = [
    "okapi", "tungsten", "marzipan", "glockenspiel", "quokka", "saffron",
    "obsidian", "kazoo", "narwhal", "paprika", "zeppelin", "bergamot",
    "axolotl", "cobalt", "harpsichord", "pangolin", "vermilion", "tamarind",
    "sextant", "lichen", "gondola", "ptarmigan", "cardamom", "bassoon",
    "wombat", "quartz", "trebuchet", "persimmon", "ocarina", "capybara",
]

# MEASURED, not chosen: on the lowest paid ChatGPT plan a 32,229-character
# file reached the model whole and a 122,243-character one lost its middle,
# keeping ~33-41K (item 182). 30K leaves margin under the one size known to
# pass. A lite file over it is refused rather than written, because the
# failure it risks is silent: the model still sees the end marker.
LITE_MAX_CHARS = 30_000

# ChatGPT's own limit on a custom GPT's instructions, and on a Project's.
# Over it the builder refuses to save, so the check belongs here, before the
# operator finds out by pasting.
GPT_INSTRUCTIONS = ROOT / "data/prompt-provider/gpt-instructions.txt"
GPT_INSTRUCTIONS_MAX_CHARS = 8_000

# What the lite file keeps of the two raw guidance blocks. Hourly: the
# series a short forecast speaks from — temperature, rain and its timing,
# gusts, thunder energy, sky. Dropped: sustained wind (the scored gust is
# the reader's number), direction (WIND DIRECTION is pre-computed) and
# pressure (no lite field reads it).
LITE_HOURLY = (
    "temperature_2m", "precipitation_probability", "precipitation",
    "wind_gusts_10m", "cape", "cloud_cover",
)
LITE_DAILY = (
    "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
    "precipitation_probability_max", "windgusts_10m_max", "cape_max",
    "cloud_cover_mean",
)
# Today and the next three days: what the reader's "next three days" line
# covers. Day+3 and Day+7 calls read EXTRACTED PER-MODEL PREDICTIONS, which
# the lite file keeps whole.
LITE_DAILY_DAYS = 4
LITE_TRACK_COLUMNS = (
    "model", "lead_time_days", "rolling_10_rain_pct", "rolling_30_rain_pct",
    "rain_pct_trend", "all_time_checks", "all_time_rain_pct",
    "avg_wind_error_kmh_10", "avg_temp_high_error_c_10", "avg_temp_low_error_c_10",
)
# Yesterday's verification is the full forecast's first section and the
# reader-first reply has none. Historical notes predate the current record.
LITE_DROPPED = {"PRE-COMPUTED VERIFICATION RESULTS", "HISTORICAL NOTES"}
HOURS_AHEAD = "HOURS AHEAD"
GUIDANCE = "TODAY'S MULTI-MODEL GUIDANCE"
TRACK_RECORD = "MODEL TRACK RECORD"
EXTRACTED = "EXTRACTED PER-MODEL PREDICTIONS"
REVIEW = "LONG-RUN REVIEW"
ESTABLISHED = "established"

NULL_CELL = "-"
FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def end_marker(text: str, label: str) -> str:
    """The last line a reader's model must quote to prove the tail arrived.

    It carries the length and a digest of everything before it, so a model
    with a code tool can verify the whole, and one without can at least
    prove it saw the end. It does NOT prove the middle arrived — ChatGPT's
    web view keeps the head and the tail — which is what the checkpoints
    are for.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:MARKER_HASH_CHARS]
    return f"OLW-DATA-END {label} chars={len(text)} sha256={digest}"


def _code_words(source: str, seed: str) -> list[str]:
    for word in CODE_WORDS:
        if word in source.lower():
            raise SystemExit(f"code word {word!r} occurs in the source text")

    words = CODE_WORDS[:]
    random.Random(seed).shuffle(words)
    return words


def _insert_checkpoints(
    source: str, count: int, every: int, words: list[str]
) -> tuple[str, list[tuple[int, int, str]], int]:
    """`source` up to its `count`-th checkpoint, with a checkpoint line after
    every `every` characters, placed at the next line break so no data line
    is split. Returns the text, (number, offset, word) per checkpoint, and how
    much of `source` it consumed.
    """
    if count > len(words):
        raise SystemExit("more checkpoints than code words")

    out: list[str] = []
    key: list[tuple[int, int, str]] = []
    written = 0
    cut = 0
    for n in range(1, count + 1):
        end = source.find("\n", n * every)
        if end == -1:
            raise SystemExit("source text shorter than the checkpoints")

        out.append(source[cut:end + 1])
        written += end + 1 - cut
        cut = end + 1

        line = f"=== CHECKPOINT {n:02d}/{count} · after {written:,} characters · code word: {words[n - 1]} ===\n"
        key.append((n, written, words[n - 1]))
        out.append(line)
        written += len(line)

    return "".join(out), key, cut


def build_probe(
    source: str, seed: str, chars: int = PROBE_CHARS, every: int = CHECKPOINT_EVERY
) -> tuple[str, list[tuple[int, int, str]]]:
    """Real prompt text cut to `chars`, checkpointed every `every`."""
    body, key, _ = _insert_checkpoints(source, chars // every, every, _code_words(source, seed))
    return body + end_marker(body, "probe") + "\n", key


def _blocks(user_prompt: str) -> list[tuple[str, str]]:
    """The message split into (header, text) in order, by the prompt-size
    module's own header convention, so the two cannot disagree about where
    a block starts."""
    out: list[tuple[str, str]] = []
    offset = 0
    for name, size in prompt_block_sizes(user_prompt).items():
        if "/" in name:
            continue
        out.append((name, user_prompt[offset:offset + size]))
        offset += size

    return out


def _cell(value) -> str:
    if value is None:
        return NULL_CELL
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\t", " ").replace("\n", " ")


def _table(columns: list[str], rows: list[list]) -> str:
    """Item 176's contract: a header row, tab-separated, "-" for null. The
    null marker is not cosmetic — an empty cell made a reader take the
    neighbouring column's value in item 176's reading test."""
    lines = ["\t".join(columns)] + ["\t".join(_cell(v) for v in row) for row in rows]
    return "\n".join(lines)


def _json_body(block: str):
    """The object a JSON block carries, from below its header line."""
    body = block.split("\n", 1)[1]
    return json.loads(body[body.find("{"):body.rfind("}") + 1])


def _series(arrays: dict, variables: tuple[str, ...], days: int | None = None) -> str:
    """Parallel arrays as one row per series, the first row the time axis.

    Models are read off the first variable's keys, so a variable whose name
    is a prefix of another ("precipitation", "precipitation_probability")
    matches only its own series.
    """
    models = [k[len(variables[0]) + 1:] for k in arrays if k.startswith(variables[0] + "_")]
    width = days if days is not None else len(arrays["time"])
    rows = [["time"] + arrays["time"][:width]]
    for variable in variables:
        for model in models:
            values = arrays.get(f"{variable}_{model}")
            if values is not None:
                rows.append([f"{variable}_{model}"] + values[:width])

    return "\n".join("\t".join(_cell(v) for v in row) for row in rows)


def _lite_hours_ahead(block: str) -> str:
    hourly = _json_body(block)["hourly"]
    return (
        "HOURS AHEAD (hour-by-hour guidance per model, from the issuance hour forward and INTO TOMORROW — "
        "read the date on each column, never the hour alone. ONE ROW PER SERIES, TAB-SEPARATED: the first row "
        "is the local time of each column, every later row is one variable for one model, named "
        "variable_model. \"-\" means no value. Temperature °C, precipitation mm, probability %, gusts km/h, "
        "CAPE J/kg, cloud cover %):\n"
        f"{_series(hourly, LITE_HOURLY)}\n\n"
    )


def _lite_daily(block: str) -> str:
    daily = _json_body(block)["primary_extended_daily"]["daily"]
    return (
        f"DAILY GUIDANCE (per model, for the location itself, the first {LITE_DAILY_DAYS} days of CALENDAR. "
        "ONE ROW PER SERIES, TAB-SEPARATED: the first row is the date of each column, every later row is one "
        "variable for one model. \"-\" means no value. Temperature °C, precipitation mm, probability %, "
        "gusts km/h, CAPE J/kg, cloud cover %):\n"
        f"{_series(daily, LITE_DAILY, LITE_DAILY_DAYS)}\n\n"
    )


def _lite_extracted(block: str) -> str:
    """Kept whole, under a lite header. Archives before olw_core 43d3d03
    (2026-09-23) carry it as JSON at ~8.5K; it is re-rendered through the
    production table writer, so an old day reads exactly as a new one."""
    from openlocalweather.llm.prompt import _table as production_table

    body = block.split("\n", 1)[1].strip("\n")
    if body.startswith("{"):
        body = production_table(json.loads(body), group_column="lead")

    return (
        "EXTRACTED PER-MODEL PREDICTIONS (pre-computed by code: each model's own call, and these exact values "
        "are scored. ONE ROW PER MODEL PER CALL, TAB-SEPARATED, \"-\" means no value. The \"lead\" column says "
        "which call: day0, day3, day7, or secondary_day0 for the second point. A model missing from a lead does "
        "not forecast that far. wind_kmh is the GUST, sustained_wind_kmh the sustained wind; never mix them):\n"
        f"{body}\n\n"
    )


def _lite_track_record(block: str) -> str:
    """Rain skill and the three error means per model per lead. Parses both
    forms: JSON before olw_core 43d3d03 (2026-09-23), a table since."""
    lines = block.split("\n", 1)[1].strip("\n").split("\n")
    if lines[0].lstrip().startswith("["):
        entries = json.loads("\n".join(lines))
    else:
        columns = lines[0].split("\t")
        entries = [dict(zip(columns, row.split("\t"))) for row in lines[1:] if row.strip()]

    rows = [[e.get(c) for c in LITE_TRACK_COLUMNS] for e in entries]
    return (
        "MODEL TRACK RECORD (pre-computed by code: each model's recent and long-run rain hit rate and mean "
        "errors, ONE ROW PER MODEL PER LEAD TIME, TAB-SEPARATED, \"-\" means no value. Error columns are "
        "OBSERVED MINUS FORECAST: positive means the model came in too LOW):\n"
        f"{_table(list(LITE_TRACK_COLUMNS), rows)}\n\n"
    )


def _lite_review(block: str) -> str:
    """Only the ESTABLISHED findings: kind, check count and claim. A provisional
    finding may not be upgraded and a short reply has no room to carry the
    qualification, so the lite forecaster is not handed one."""
    findings = [f for f in _json_body(block)["findings"] if f.get("confidence") == ESTABLISHED]
    rows = [[f.get("kind"), f.get("checks"), f.get("claim")] for f in findings]
    return (
        "LONG-RUN REVIEW (computed in code over the whole stored record: the ESTABLISHED findings only. These "
        "are the only model rankings and biases you may use; do not derive your own from the track record):\n"
        f"{_table(['kind', 'checks', 'claim'], rows)}\n\n"
    )


def render_lite(user_prompt: str, seed: str) -> tuple[str, list[str]]:
    """The lite data message: the archived message's pre-computed blocks
    verbatim, its raw guidance tabulated and narrowed, its record cut to rain
    skill and established findings, checkpointed throughout. Returns the text
    and the code words in order."""
    transforms = {
        HOURS_AHEAD: _lite_hours_ahead,
        GUIDANCE: _lite_daily,
        EXTRACTED: _lite_extracted,
        TRACK_RECORD: _lite_track_record,
        REVIEW: _lite_review,
    }
    parts: list[str] = []
    for name, text in _blocks(user_prompt):
        if name in LITE_DROPPED:
            continue
        parts.append(transforms[name](text) if name in transforms else text)

    body = "".join(parts)
    if "olw_blend" in body:
        # The standing rule: the blend never sees its own record.
        raise SystemExit("olw_blend appears in the lite data")

    words = _code_words(body, seed)
    checked, key, consumed = _insert_checkpoints(body, len(body) // CHECKPOINT_EVERY, CHECKPOINT_EVERY, words)
    checked += body[consumed:]
    text = checked + end_marker(checked, "lite") + "\n"
    if len(text) > LITE_MAX_CHARS:
        raise SystemExit(f"lite data is {len(text):,} characters, over the {LITE_MAX_CHARS:,} budget")

    return text, [w for _, _, w in key]


def _issuance(day: str, index: int) -> dict:
    return json.loads((ROOT / f"data/prompts/{day}.json").read_text(encoding="utf-8"))["issuances"][index]


def _probe(day: str, out_file: Path, chars: int, every: int) -> None:
    issuances = json.loads((ROOT / f"data/prompts/{day}.json").read_text(encoding="utf-8"))["issuances"]

    # One issuance is ~80K characters since items 174 and 176, short of the
    # probe, so the day's issuances are joined. Repeated blocks are fine: the
    # test is how far a model can see, not what it makes of the content.
    source = "\n".join(i["user_prompt"] for i in issuances)
    # Seeded by the file name too, so two probes of one day do not share an
    # order. They share words, and a position can coincide by chance —
    # CHECKPOINT 02 is persimmon in both of the first two — so each must be
    # run in a chat that cannot recall the others. The first probe.txt
    # (7d68894) was seeded with the bare date and no longer regenerates byte
    # for byte; the committed file is its own answer key.
    text, key = build_probe(source, seed=f"{day}/{out_file.name}", chars=chars, every=every)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(text, encoding="utf-8")

    print(f"{out_file}: {len(text):,} characters, {len(key)} checkpoints")
    for n, offset, word in key:
        print(f"  {n:02d}  {offset:>8,}  {word}")
    print(f"  end  {text.splitlines()[-1]}")


def _lite(day: str, out_file: Path, index: int) -> None:
    instructions = len(GPT_INSTRUCTIONS.read_text(encoding="utf-8"))
    if instructions > GPT_INSTRUCTIONS_MAX_CHARS:
        raise SystemExit(f"{GPT_INSTRUCTIONS.name} is {instructions:,} characters, over {GPT_INSTRUCTIONS_MAX_CHARS:,}")

    issuance = _issuance(day, index)
    text, words = render_lite(issuance["user_prompt"], seed=issuance["issued_at"])

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(text, encoding="utf-8")
    print(f"{out_file}: {len(text):,} characters from {len(issuance['user_prompt']):,}, "
          f"issued {issuance['issued_at']}, code words {' '.join(words)}")


# The lite forecaster's rows, named apart from `olw_blend` so the two are
# never pooled: they are different forecasters on different inputs.
LITE_MODEL_ID = "olw_lite"
SCORED_LEADS = (0, 3, 7)
BACKTEST_ISSUANCE = 0


def _reply_call(reply: str) -> dict:
    """The LAST fenced JSON block in a reply — the submission block ends it."""
    blocks = FENCED_JSON.findall(reply)
    if not blocks:
        raise ValueError("no fenced json block")
    return json.loads(blocks[-1])


def _score(replies_dir: Path) -> int:
    """Scores each `<date>.txt` reply's call beside the day's first issuance.

    SAME SCORER, SAME CONVERSION, SAME ROWS as production: the call becomes a
    ModelPrediction through the pipeline's own `_blend_prediction` and
    `_extended_blend_predictions` — private, and imported anyway, because a
    copy here would be a second definition of what the blend committed to —
    and every row is scored by `score_prediction` against the same actuals.
    The full forecaster and the models come from the log's FIRST issuance,
    which is the one the lite data is rendered from.
    """
    from openlocalweather.llm.schema import GeminiJudgmentResponse
    from openlocalweather.models import DailyLogEntry
    from openlocalweather.pipeline import _blend_prediction, _extended_blend_predictions
    from openlocalweather.store.actuals_cache import read_actuals_cache
    from openlocalweather.verify.scoring import resolve_prediction_rows, score_prediction

    actuals = read_actuals_cache(ROOT / "data").primary
    tally: dict[tuple[str, int], list] = {}

    for path in sorted(replies_dir.glob("*.txt")):
        day = path.stem
        issuance = _issuance(day, BACKTEST_ISSUANCE)
        _, words = render_lite(issuance["user_prompt"], seed=issuance["issued_at"])

        try:
            call = _reply_call(path.read_text(encoding="utf-8"))
            judgment = GeminiJudgmentResponse.model_validate(
                {k: call[k] for k in ("today_properties", "extended_properties") if k in call}
            )
        except (ValueError, KeyError) as e:
            print(f"{day}  UNPARSED: {e}")
            continue

        seen = call.get("checkpoints") or []
        missing = [w for w in words if w not in seen]
        print(f"{day}  checkpoints {len(words) - len(missing)}/{len(words)}"
              + (f", MISSING {' '.join(missing)}" if missing else "")
              + (f", EXTRA {' '.join(w for w in seen if w not in words)}" if set(seen) - set(words) else ""))

        entry = DailyLogEntry.model_validate_json((ROOT / f"data/log/{day}.json").read_text(encoding="utf-8"))
        first = resolve_prediction_rows(entry)[BACKTEST_ISSUANCE].predictions
        lite = {
            0: [_blend_prediction(judgment.today_properties)],
            3: _extended_blend_predictions(judgment.extended_properties, 3),
            7: _extended_blend_predictions(judgment.extended_properties, 7),
        }

        for lead in SCORED_LEADS:
            target = (date.fromisoformat(day) + timedelta(days=lead)).isoformat()
            actual = actuals.get(target)
            rows = [p.model_copy(update={"model": LITE_MODEL_ID}) for p in lite[lead]] + first.for_lead(lead)
            for p in rows:
                score = score_prediction(p, actual, lead)
                if score is not None:
                    tally.setdefault((p.model, lead), []).append((day, p, score))

    _print_tally(tally)
    return 0


def _print_tally(tally: dict[tuple[str, int], list]) -> None:
    def mean_abs(values):
        values = [abs(v) for v in values if v is not None]
        return f"{sum(values) / len(values):.1f}" if values else "-"

    def mean(values):
        values = [v for v in values if v is not None]
        return f"{sum(values) / len(values):.3f}" if values else "-"

    for lead in SCORED_LEADS:
        print(f"\nDay+{lead}   model            n  rain   brier  |high|  |low|  |gust|")
        rows = sorted(((m, s) for (m, l), s in tally.items() if l == lead),
                      key=lambda r: -sum(x[2].rain_correct for x in r[1]) / len(r[1]))
        for model, scored in rows:
            hits = sum(x[2].rain_correct for x in scored)
            print(f"        {model:15s} {len(scored):2d}  {hits:2d}/{len(scored):<2d} "
                  f"{mean([x[2].rain_brier for x in scored]):>6}  "
                  f"{mean_abs([x[2].high_error_c for x in scored]):>5}  "
                  f"{mean_abs([x[2].low_error_c for x in scored]):>5}  "
                  f"{mean_abs([x[2].wind_error_kmh for x in scored]):>5}")


def main(argv: list[str]) -> int:
    if argv[:1] == ["probe"] and len(argv) in (3, 5):
        sizes = [int(a) for a in argv[3:]] or [PROBE_CHARS, CHECKPOINT_EVERY]
        _probe(argv[1], Path(argv[2]), *sizes)
        return 0

    if argv[:1] == ["lite"] and len(argv) in (3, 4):
        _lite(argv[1], Path(argv[2]), int(argv[3]) if len(argv) == 4 else -1)
        return 0

    if argv[:1] == ["score"] and len(argv) == 2:
        return _score(Path(argv[1]))

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
