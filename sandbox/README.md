# Sandbox — the same code, somewhere the weather is different

Kisumu is a mild, consistent place, and that has hidden defects. Every one of
these survived a full test suite because the deployment never produced the case:

- `TEMP_CHANGE_BANDS_C` had no ceiling, so 6 °C and a 25 °C frontal passage
  were the same three words. Nothing here has ever cleared 7 °C.
- The wind warning bands are dormant. The record's windiest day is 28.4 kt of
  gust, and gale force starts at 34.
- The airport station has never filed a gust group, so convective gusts cannot
  be observed here at all.
- Two consecutive gales would read "similar winds", which no local day tests.

**This runs the deterministic half of the pipeline against a fleet of
locations and reports which code paths fired.** It answers one question: what
does this deployment's weather hide?

## What it costs

Nothing. `verify/` contains no LLM reference — fetching model predictions,
fetching observations and scoring them are entirely LLM-free, and Open-Meteo's
forecast and archive APIs need no key. Three requests per location.

The narrative is the only part that costs money, and it is not run here. To
see what a sandbox location's forecast would READ like, render its prompt and
put it through the manual harness in item 77 rather than calling a provider.

## What it does not do

- **No blend.** `olw_blend` comes from the LLM's own call, so a fleet without
  one has no blend to score. That is deliberate: the fleet exists to exercise
  the deterministic labels, and a stubbed blend would put fiction in a record.
- **No stored record.** Nothing is written. This is a diagnostic sweep, not a
  second production pipeline.
- **One day.** It compares today's consensus against yesterday's observations,
  which is what the Overview does. Accumulating a fleet record over time is a
  larger thing and is not this.

## Running it

    .venv/bin/python sandbox/sweep.py

Add `--locations` to narrow it. The exit code is 0 whatever it finds: this
reports, it does not judge.

## Accumulating

    .venv/bin/python sandbox/sweep.py --store

writes `sandbox/data/<location>/<date>.json` — the per-model Day+0, Day+3 and
Day+7 predictions, yesterday's observations, and the labels computed from
them. `.github/workflows/sandbox.yml` does this daily and commits the result.

**The predictions are the point, not the labels.** Labels can be recomputed
from the record whenever the code changes, and are stored only so that such a
change shows up as a diff. The predictions cannot be recovered later at all —
Open-Meteo's forecast endpoint only ever answers about now — so a day not
stored is a day gone.

**The date is the LOCATION's, taken from the returned series, never the
runner's clock.** A sweep at 22:00 UTC receives Wellington's tomorrow; filing
that under the runner's date would score it three days later against the wrong
observations, and nothing would look wrong until the accuracy figures already
were.

Deliberately not under `data/`: that is the production record, and twelve
places' weather in it would corrupt one location's accuracy page.

## What this is not ready for yet

Rendering a NARRATIVE for a sandbox location. A user prompt needs a track
record, verification results and historical notes, and a location with no
history has none — see ROADMAP item 96. Accumulating is what fixes that,
which is the other reason to run this daily.
