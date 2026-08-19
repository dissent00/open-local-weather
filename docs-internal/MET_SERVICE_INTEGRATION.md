# Adding a national met service as a scored model

A procedure, derived from doing it once for Kenya. The point of writing it
down is that roughly 200 national met services exist and this project wants
most of them — so the parts that generalise need separating from the parts
that were only ever about Kenya.

## Why bother

The national met service is the one forecast source with local knowledge no
global model has: local topography, local convective behaviour, forecasters
who have watched this specific place for decades. It is also a forecast that
can be wrong. This project's whole argument is that the second sentence is
settled by measurement rather than by deference — so the met service goes
through the same accuracy loop as GFS and ECMWF, and earns whatever its
record says it earns.

## What generalises

### 1. The controlled-vocabulary insight

**This is the finding that makes the whole thing cheap.** Met-service prose
looks like free text, but many services publish a **glossary defining their
own terms quantitatively**, usually in the bulletin itself. Kenya's daily
bulletin ends with:

| Term | Meaning |
|---|---|
| Light / Moderate / Heavy / Very Heavy | <5mm / 5–20mm / 21–50mm / >50mm |
| Few / Several / Most places | <33% / 33–66% / >66% of area |
| Isolated / Scattered / Numerous / Widespread | <25% / 25–50% / 51–70% / >70% |
| Possible / Chance of / Likely / Expected / Very Likely / Certain | 10–30% / 31–50% / 51–75% / 76–90% / 91–99% / 100% |

Decoding a documented encoding is a different problem from interpreting
natural language. It means **no LLM call**, which matters at 200 services:
deterministic parsing costs nothing per run, never drifts, and can be tested
against captured fixtures.

WMO guidance has shaped terminology across many national services, so
expect similar ladders elsewhere — but **find and read the actual glossary
before assuming the thresholds match**. "Likely" is not universally 51–75%.

### 2. Pick the product by lead time, not by prominence

The flagship product is usually the wrong one. Kenya's weekly 7-day forecast
is the most visible, and it is the worst choice:

- Its entry for a Thursday is a four-day-old forecast. Scoring that against
  models' fresh same-morning runs understates the service for reasons that
  have nothing to do with skill.
- It is periodically published as scanned images (18–24 Aug 2026: **0**
  extractable characters, against ~8,600 in each of the five prior weeks).

The **daily** bulletin is issued ~3pm for 9pm-to-9pm next day — a genuine
Day+0 — and the **five-day** grid supplies Day+3. Look for the products that
match the lead times you already track.

### 3. Anchor lead time on the bulletin's own issue/validity date

Never assume "the newest bulletin is for today". Read the validity date out
of the document and only accept a prediction into a lead-time slot when it
actually covers that target date. A service that issues at 3pm means a
morning run will often find yesterday's bulletin still newest — scoring it
as today's forecast credits or blames them for a different day's weather.

### 4. Store the extract, not the document

The full daily PDF is ~8,700 characters, of which a handful of lines concern
any one location. Compose a location-relevant extract instead: the area's own
row, national-outlook sentences naming it, the multi-day rows. Roughly a 90%
reduction in both stored bytes and prompt tokens.

Keep the service's **own words**, not just decoded booleans. "Moderate
showers" is better narrative input than `rain=True`, and storing the real
phrasing is what lets the parser's rules be revisited against what was
actually published.

### 5. Predictions are not recoverable

Actuals can always be re-fetched from an archive. A met service's forecast
cannot: most replace their bulletin rather than archiving per-day issues.
Every run that fails to store it destroys the only copy. Store from day one,
including the "unavailable" cases — a failed fetch and a day the fetcher
never ran must not look alike.

### 6. Sparse fields are absent, not zero

Met bulletins typically give rain and temperature but no wind, pressure or
onset. Those stay `None`, which reads as "not forecast" — never as "no rain"
or "calm". The accuracy page already renders a null as a dash with an
explicit note that a blank is not a missed call.

### 7. Architecture: nothing else has to change

- Implement `fetch()` for the narrative text (existing `BulletinFetcher`
  Protocol) and `fetch_forecast()` for the structured half. The pipeline
  duck-types the second, so a service that can't be decoded keeps working as
  narrative-only.
- Emit a `ModelPrediction` with `model=<your model id>`.
- Add the id via `defaults.scored_models()`. **Do not add it to `MODELS`** —
  that constant is the Open-Meteo `models=` API parameter and nothing may go
  in it that Open-Meteo can't serve.
- Scoring, rolling windows, track record, weekly review, findings gating and
  the accuracy page all then treat it identically, with no further wiring.

## What does not generalise

- **The scraping.** Landing page → post → PDF is Kenya's shape. Others serve
  HTML, JSON, or a PDF straight off a fixed URL.
- **The table layout.** Kenya's five-day grid has county blocks that straddle
  page boundaries, with labels in column 1 or 2 depending on the row. That is
  a quirk of one document, but the *class* of problem is not — flatten pages
  into one row stream so a page break is invisible.
- **The area key.** County here; region, district, province, or a named city
  elsewhere. Config carries `local_bulletin_area_name`.
- **The rain rule.** Kenya's cutoff sits at its own "more probable than not"
  boundary (Likely, 51%). Another service's ladder may divide differently.

## The judgment call worth repeating

Do **not** multiply a probability term by an area term to get a
point-probability. "Rain expected over few places" means high confidence of
rain somewhere in under a third of a region, which is not the chance of rain
at one airport, and no honest arithmetic turns one into the other. Pick a
defensible rule, record the discarded term alongside it, and let the
verification record settle whether the rule was right. Putting the met
service through the accuracy loop is exactly what makes that question
answerable instead of arguable.

## Checklist

1. Find every forecast product and its issue cadence; pick by lead time.
2. Confirm text extraction on **several** past issues, not one.
3. Find the glossary. Record the thresholds in code, with a comment citing
   the source.
4. Identify the area key and confirm the location appears under it.
5. Parse validity/issue date from the document.
6. Emit `ModelPrediction`; leave unforecast fields `None`.
7. Compose a location-relevant extract for storage and prompting.
8. Capture a real bulletin as a test fixture and test against it.
9. Register the model id through `scored_models()`.
10. Let the record decide whether it is any good.
