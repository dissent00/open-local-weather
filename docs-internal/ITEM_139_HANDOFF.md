# Item 139 stage 3: handoff, 2026-09-29

For the next session. The reasoning lives in ROADMAP item 139 ("Design,
2026-09-29: the forecaster's call means the next 24 hours" and "Stage 3,
the build order") and item 188; this file is the working state and the
next moves. Delete it when stage 3 ships.

Read first: the ROADMAP working order (top of the file), item 139, item
188, AGENTS.md. Standing rules: design in prose and ask before building;
failing test first; read your diff as a separate act; Python, then
vectors, then Dart, then push, then re-pin Ensemble; check CI after
pushing. A deployment runs 1 or 100 forecasts a day at any hour: never
reason in morning and evening runs.

## Where it stands

Every forecast is scored on its own 24 hours from its issue hour (its
window); a period is the local date a forecast was issued, and each period
counts once. Built and pushed, **read by nothing published**. The page,
`track_record.json` and every prompt block still use the calendar record,
row 0 only.

| Step | What | Commit |
|---|---|---|
| 2 | Window pass on every run, archive asked only when a window is due; every forecast's Day+3/+7 | 081c843 |
| 2 | A window's onset measured from its opening, not the clock | 9089e00 |
| 3a | `derive_period_track_record` | 266d188 |
| 3b | A window claim from every Day+0 source; `olw backfill-window-claims`; Kenya Met on its bulletin's 21:00-21:00 | e32072a |
| 3b | Recheck scored windows when the station fills in late | e4e9a33 |
| review | Archive fetched from the evening before the oldest entry | 202f7ed |
| 3c | `window_observed` on each scored row (schema, `run_row.json`, Dart row creator) | 5c9d050, 2a6d17f |
| 3c | `build_period_review` | 8e11075 |
| 188 | Local met services designed on their own terms | b414708 |
| 3d | `wind_checks` in both languages; `window_gust_corrections` | efad4cf |

Ensemble is pinned to efad4cf (ensemble 0607b23). Full suite 1,728 passed;
`flutter test` in `app/olw_core` 224 and in Ensemble 268.

## Next: step e, the forecaster's call means the next 24 hours

Approved 2026-09-29. It lands with step f, on one date: once the call
means the window, the calendar series would score it against the wrong
quantity. Build it so nothing published changes until f.

1. **Harness first.** Archived issuances issued after local noon, old
   prompt against new. Measure how many returned `temp_high_c` sit nearer
   the window's high than today's calendar high; it is 3 of 10 today.
   `data/prompts/<date>.json` keeps each issuance's `user_prompt` and the
   system prompts' hashes. `tools/harness_inputs.py` builds the NARRATIVE
   pair and re-renders the archived blocks through today's
   `build_user_prompt`; this measurement is the JUDGMENT call, so pair
   `build_judgment_prompt` with the re-rendered user prompt the same way.
2. **Prompt, both languages** (`llm/prompt.py`, `app/olw_core/lib/src/llm/prompt.dart`,
   vectors `llm_system_prompt.json`, `llm_user_prompt.json`):
   - the judgment block `today_props`: every scored field describes the
     24 hours from the window's opening; "rain ... during the day" goes;
   - the narrative's "ONE VALUE PER QUANTITY PER DAY": the high is the
     next 24 hours', named by its day when that is not today;
   - reverse "THE CALL YOU WERE GIVEN describes the WHOLE calendar day";
   - `rain_expected` and `onset_window` name the day when the onset falls
     after midnight ("Tomorrow Afternoon Showers");
   - EXTRACTED PER-MODEL PREDICTIONS: the `day0` rows become the models'
     window claims (the five models only, never the yardsticks or the code
     blend), under a lead named for the next 24 hours;
   - CALIBRATED PEAK GUST: the models' window claims corrected by
     `window_gust_corrections(period_windows_as_of(...))`.
3. **The disagreement tests and footnotes** (`disagreement.py`,
   `observed.py`; `_standing_call`, `_information_moved`,
   `_overnight_low_is_settled` in `pipeline.py`). They decide whether a run
   earns an LLM call. Reduce the station over the standing call's own hours
   (its window opening to now; `station_weather_within`), and make the
   warmer-low gate "the window's night is over".
4. **Tile:** keep "High / Low"; the temperature modifier is silent when the
   window's high is not today's (`_tile_comparison`, `tiles.py`,
   `tiles.dart`).
5. **The blend's window claim:** `_blend_prediction(tp)` joins the row's
   window claims from the switch date.
6. **olw_core:** the prompt text, the tile, and the window extraction the
   prompt needs (`extract_window_predictions`), each with vectors; re-pin.

## Then: step f, the switch, one commit

From item 139 (f), with what this session learned:
- `track_record.json` from `derive_period_track_record` with an **empty
  prior**, or its all-time guard refuses every Day+0 row (49 calendar
  checks against 13 periods). Freeze the calendar record in its own file,
  shown as "calendar day, before <date>", never averaged in.
- MODEL TRACK RECORD, PRE-COMPUTED VERIFICATION RESULTS (the latest scored
  period), the review (`build_period_review` for `build_weekly_review` in
  `pipeline.py` and `cli.py`), the calibration, the code blend (the window
  claim is the scored Day+0), coverage (every row), the accuracy page.
- Kenya Met leaves the models' ranking for its own section (item 188,
  point 2); the page says an area forecast is verified at a point (point 4).
- The record re-derives on any run that scored something, not only a
  day's first issuance.
- Brier reaches the track record. Any new `TrackRecordEntry` field reaches
  the prompt the moment it exists (`_track_record_payload`); add them here.
- Harness at the prompt seam before pushing.

After the switch: item 188 points 1 and 3, then stage 4 (the re-run figure)
and stage 5 (the app scores windows).

## To check

- **The 15:01Z run of 09-29** is the first live run with steps a-d. Its row
  should hold persistence, climatology and `olw_code_blend` in
  `window_predictions`. It was still in its Forecast step at 15:31, most
  likely a queued write-up's 30-minute poll.
- **The next run after local midnight** is the first to score windows live
  with this code: the 09-28 windows fall due, so the recheck, Kenya Met's
  own window and `window_observed` all run. Check the rows it rewrites.
- `olw window-record` shows the window record beside the published one.

## Traps met this session

- zsh does not split an unquoted variable: a mutation loop passed
  "file -k name" as one argument and ran no tests. Quote each argument.
- A claim's window can open the day before its entry (the bulletin's
  21:00): fetch from `WINDOW_OPENS_DAYS_BEFORE_ENTRY` earlier.
- HKKI's archive fills a day or two late; a window scored early is
  rechecked on the next fetch, never without the station's reports.
- `make_log_lookup` memoizes: create it after anything writes entries.
- Changing `IssuancePredictions` regenerates `run_row.json` and
  `spec/day_entry.schema.json`, and Dart's `RunRecord.create` must emit
  the same keys in the same order.
- The forecast workflow rebases its push, so code pushes during a run are
  safe; do not write `data/log` while one runs.

## Commands

```
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python spec/export_vectors.py && .venv/bin/python spec/export_entry_schema.py
cd app/olw_core && flutter test && flutter analyze
.venv/bin/olw window-record
.venv/bin/olw backfill-window-claims --dry-run
```

Re-pin: set `ref:` in Ensemble's `pubspec.yaml` to the full hash of the
last commit that changed `app/olw_core`, `flutter pub get`,
`flutter test --exclude-tags live`, commit, `git push origin HEAD:main`.
