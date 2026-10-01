# The floor: options for item 190

Written 2026-10-01, after item 189 shipped. The floor is the write-up code
writes on every run, in both repositories, so no day shows "Write-up
unavailable" and a keyless install has prose under its tiles. Item 190's
text in `ROADMAP.md` already specifies a v1; this lays out the choices
around it, with a recommendation, so you can pick before anything is built.

Decisions that are yours: the floor's shape (A, B or C), where it shows (P1
or P2), the three label rules the reader-words list exposed today, and the
sign-off line.

## 1. What exists to build from

Every input below is on the stored entry or computed by a composer the run
already calls. None needs a model.

- **The served call** (item 189): rain, probability, onset, amount, high,
  low, gust, trend, the extended leads.
- **The tiles** as composed for 2026-09-30: High / Low 32° / 19°; Rain
  "Dry / No Rain"; Wind early NNE 5G8, midday SSW 7G18, evening WSW 10G19;
  Cloud early mostly cloudy, midday partly cloudy, evening mostly cloudy;
  UV 9.15 (very high); AQI 103 (unhealthy for sensitive groups) with the
  three ground stations at 103, 59 and 40; sun 06:27 and 18:34; pressure
  trend −1.6 hPa.
- **The composers**: `describe_day_rain`, `describe_day_over_day`,
  `describe_extended_trend`, `describe_convective_timing`,
  `describe_wind_shift`, `describe_wind_timeline`, `summarize_synoptic`,
  `summarize_instability`, `summarize_ground_aqi`,
  `describe_observed_so_far`, `describe_notable_disagreements`, and
  `phrase_defect` as the shape check on every sentence (item 158).
- **The review findings** (items 12 and 147), the CAP warnings (item 2),
  the met service's bulletin text, and the per-model rows for any
  disagreement sentence.

The 09-23 write-up, the best on the record, had 61 sentences: 25 restate a
composed phrase or a stored field, 32 follow from stored fields by a rule,
4 need a model.

## 2. Shape

### Option A: v1 as item 190 specifies. Recommended first.

Three sections, fixed frames over verbatim phrases, 700 to 1,100
characters. Assembled by hand for 2026-09-30 from the stored fields, so
the joins are mine and the code's will differ:

```
## Today's Forecast
Dry, with no rain expected. High 32 °C (90 °F), low 19 °C (66 °F). Sky:
mostly cloudy early, partly cloudy by midday, mostly cloudy in the
evening. Wind light from the north-north-east early, backing
south-south-west by midday, gusts to 19 km/h (10 kt) in the evening.
UV 9.2 (very high). Air quality 103 (unhealthy for sensitive groups) at
Kisumu Airport; 59 and 40 at the two other stations. Pressure falling
1.6 hPa over 24 hours.

## Extended Outlook
Day+3: dry, rain 30%. Day+7: dry, rain 40%.

## Winam Gulf — Conditions for Boaters
Light northerly early, south-westerly by midday; peak gust 19 km/h
(10 kt) in the evening.

Figures issued 06:01. Written by code; a discussion follows when a model
answers.
```

For: the smallest build; every sentence is a composer's, so the reading
gate either passes or fails fast; the record never mistakes code text for
model text (`narrative_source: code`, `narrative_llm_model` null).
Against: a template voice; no "why"; no per-model disagreement; the same
shape every day.

### Option B: the AFD shape

Option A plus the sections the write-up has: Severe Weather / Hazard
Potential (the convective flag, the storm-gust rule, CAP warnings),
Detailed Discussion with a Synoptic Overview (`summarize_synoptic`'s
statements) and the per-model disagreement as lists (rain votes with
probabilities, the high and low spread, the CAPE spread), and Forecaster
Confidence Notes from the review findings. 2,000 to 3,000 characters.

For: a reader gets most of what the discussion gave, every day, and item
191's audit can hold the model to the same facts. Against: three times the
joins; lists of numbers read as a dump; the repetition across days that
SumTime's forecasters edited out; a harder gate.

### Option C: the floor is the brief rendered

Build item 191's brief first, the structured object the writer will be
handed, and make the floor `render(brief)`. The writer gets the same
brief; the audit checks the prose against it.

For: one source of facts for floor, writer and audit; no second
extraction. Against: nothing shows until the brief's shape is settled,
which is half of item 191.

## 3. Placement

**P1: the floor replaces the placeholder.** Shown only when no write-up
has landed. Days with a write-up look as they do today. The roadmap's
reading.

**P2: the floor is always the first section.** Code writes Today's
Forecast; the model writes the discussion below it. The model's Today's
Forecast is already a realiser of locked phrases under the prompt's rules,
so P2 removes the section most prone to audit findings and shrinks the
writer's job. It changes the page, the email and the app, and belongs
with item 191.

Recommendation: P1 now, P2 with 191.

## 4. The reading gate

`olw floor --date D` renders the floor from a stored entry: no fetch, no
call. Render the eight placeholder days and five served days, run
`phrase_defect` over every sentence, and read them beside the 09-23
write-up. Go if you would publish each under "Today's Forecast" with
zero defects. If not, the joins are fixed before anything else.

## 5. The app

`olw_core` renders the same floor from the stored forecast, and the app
shows it in place of its "No written discussion" text and the placeholder,
labelled as written by code. Vectors pin the Markdown byte for byte, so
the page and the phone never say different things from the same entry.

## 6. Three label rules the reader-words list exposed today

The floor's rain sentence inherits these from the served call, so they
are worth deciding now.

1. **The onset window.** Code shows the spread of the wet models' onsets,
   which spanned 16 hours on 08-23 ("03:00 – 19:00") where the model wrote
   a three-hour window. Choose: the median alone ("From 15:00"), median to
   latest, or a cap on the spread.
2. **Runs whose window crosses midnight.** For an 18:01 issuance code
   wrote "Afternoon Showers & Thunderstorms", meaning tomorrow afternoon;
   item 158's sun words are same-day words. The label needs a day
   qualifier ("Overnight", "Tomorrow afternoon") when the onset is past
   midnight. The deployment runs at 06:01 today, so this is the app's and
   a fork's problem before it is the page's.
3. **A timing word for an hour already lived through.** Day+0 onsets are
   extracted from the calendar day's hours, so for an 18:01 run a
   consensus onset of 15:00 is behind. The onset window already drops
   it; the rain label does not, and wrote "Isolated Afternoon Showers &
   Thunderstorms" for the hours ahead on 09-22 and 09-25. The fix is the
   window's rule applied to the label's word, one line in each language
   and a vector case; it rides with 190's change unless you want it now.

## 7. Recommendation

Option A with P1, built as pure functions of named inputs so that Option C
can adopt them as the brief's fields when 191 arrives. Settle the three
label rules in the same change. Order as for 189: Python composer and the
`olw floor` command, vectors, Dart, re-pin, the app. Zero calls at every
step.

Sign-off line: "Written by code; a discussion follows when a model
answers" on a deployment with a key, "Written by code." on one without.
