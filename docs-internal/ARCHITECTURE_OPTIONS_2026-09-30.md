# Alternatives to the free-tier LLM: what the record says, and what would work

Written 2026-09-30 in a read-only session. Sources: the code in both repos;
the record (all 85 committed versions of `data/spend_ledger.json`,
`data/log/2026-09-08..30.json`); roadmap items 17, 24, 132, 172, 173, 180,
182, 186; outside pages fetched today, cited inline. Nothing was changed.

## 1. The problem, re-measured

The brief says the write-up has failed since 09-24. The record says something
narrower, and it changes what to fix.

Ledger, 03:01Z runs since the two-call split (09-12 to 09-30, 18 runs):

| call | Gemini first try | Gemini after retries | served by anyone |
|---|---|---|---|
| scored | 11/18 | 16/18 | 18/18 |
| write-up (17 asked) | 8/17 | 12/17 | 13/17 |
| write-up, since 09-22 (8 asked) | 1/8 | 4/8 | 5/8 |

"Seven straight days without a write-up" is four days with none anywhere
(09-24, 25, 28, 30) and three days (09-26, 27, 29) where the 03:01Z run wrote
one and the 15:01Z run's placeholder replaced it at the top of the entry (the
morning text is still in `earlier_issuances`). The evening run is gone as of
today, so that class goes with it. Of the record's 92 Gemini 503s, 68 fell at
15Z; every one of the 20 429s seen at 03Z followed a 15Z burst inside the same
08:00Z quota day.

Three more facts that shape the answer:

- The write-up's first attempt came 0.01 s after the scored call in every one
  of the 29 two-call runs. Since 09-22 it was refused 7 of 8 times at 03Z. The
  2-minute pause added today has zero runs of evidence; 10-01 is the first.
- The scored call is arithmetic now. The code blend (item 173) stands at 26/29
  on Day+0 rain against the LLM's 23/29, Brier 0.129 vs 0.149, the code better
  on 17 of 22 days. The LLM leads only on temperature: 0.14 C on the high,
  0.22 C on the low.
- Of the 61 sentences in the best write-up on record (09-23), 25 restate a
  composed phrase or a stored field, 32 are derivable by a rule from stored
  fields, and 4 need a language model. Three of those four are sentences the
  prompt now forbids. One legitimate LLM-only sentence per issuance.

One structural fact, verified in the code: the day's entry is written after
the LLM calls (`pipeline.py`: `_generate_forecast` at line 4030,
`write_log_entry` at 4156), and `cli.py:1042` returns 1 on an LLM failure. A
morning where every provider refuses the scored call leaves no entry, no model
rows, no code-blend row. The app has the same hole: a refused judgment stores
nothing, though extraction has already run (`forecast_runner.dart:337`).

So the LLM is load-bearing in two places it need not be: it decides the
numbers, where code does as well or better, and its failure can remove the
day's record. Its contribution is prose, and the prose job as built (~146K
characters, ~56K tokens in, thousands of words out) is the shape every free
route fails on: Gemini sheds it, OpenRouter's free models finish it 1 time in
5 (item 180), and Groq's 8K tokens per minute cannot accept it at all.

Gemini tokenises the data message at ~2.5 characters per token (measured from
`meta.input_tokens` against `meta.prompt_size` on six entries), not the ~4
item 174 assumed.

## 2. The outside, fetched 2026-09-30

| route | takes the 56K-token call | takes a ~4K-token brief | key | note |
|---|---|---|---|---|
| GitHub Models | no | no | none | Retired 2026-07-30 (github.blog changelog). No keyless LLM inside Actions any more. |
| Gemini Flash family | yes | yes | key, no card | Per-model 20 RPD is evidenced only for 3.8 Flash, via the API's own error text; the per-model table is gone from the docs; "rate limits are not guaranteed". Ten free text models. |
| Groq | no (8K TPM) | yes, ~50/day by TPD | key, no card | gpt-oss-120b/20b, qwen3.8-27b; strict JSON schema. |
| Mistral free mode | probably | probably | key, no card | Limits unpublished; training on by default with an opt-out toggle. |
| SambaNova | ~4/day by TPD | 20/day | key; card status contested since 2026-08-12 | 128K context. |
| Cloudflare Workers AI | 6 to 22/day | 60 to 230/day | account and a deploy, no key in code | 10,000 neurons/day; cron triggers on the free plan. |
| Cerebras, Hugging Face | no | no | card / $0.10 a month | No permanent free tier. |
| Cohere trial | shape yes | yes | key | Terms forbid production use. |
| Actions runner, local model | ~30 to 60 min | ~3 min | none | Public repo: 4 vCPU, 16 GB, unlimited minutes, 6 h per job, 10 GB cache. Throughput inferred from the nearest measured hardware, not measured on a runner. |

Read as one line: nothing free takes the call as built; almost everything free
takes a small one; the only route with no key at all is the runner the
project already uses.

## 3. Where every design converges

Seven angles were designed independently (five completed in the session; the
deterministic-only and Gemini-only angles are covered in section 4 from the
same maps). Whatever the angle, the same spine came out:

1. Numbers before any LLM. Write the entry, publish the tiles and the scored
   rows, then ask for prose. Removes the no-entry failure in the pipeline and
   the app.
2. The code blend is the served call (item 173 point 1). The LLM's own call
   becomes an optional, hidden, scored row when a key exists, so the "does the
   LLM beat arithmetic" series continues at no reliability cost.
3. A code-written write-up as the floor, replacing "Write-up unavailable".
   Every sentence a composed phrase the page already trusts. Labelled as
   code-written; `narrative_llm_model` stays null.
4. The LLM job shrinks to a writer: a brief of ~10K characters (the lite
   package cut further) plus a ~5K-character writer prompt; Markdown out;
   ~600 words. About 4K tokens in, under 1K out. It never sees raw model
   arrays, so the half of the 39K-character narrative prompt that exists to
   stop the model contradicting code goes.
5. An audit on the prose, in code: headings in order; every number in the
   output present in the brief; the locked phrases verbatim; `phrase_defect`
   per paragraph. A failed audit falls to the next route or the floor.
6. Provenance on the entry: who wrote the prose, from which brief.

What differs between the designs is only where the writer runs.

## 4. The writer routes, judged

**A. Gemini only, made cheap (brief option 1; today's configuration).**
The 2-minute pause, 3 tries at +3/+10 min, a second chance at +1h.
Unmeasured; the record's 03Z rates suggest 50 to 70% with retries. A lighter
Gemini model as a second bucket is plausible, but its RPD is unverified and
whether 503 shedding is per model is unknown. A shorter prompt may or may not
lower Gemini's own refusals; size-blindness is untested. Ceiling: one vendor
with no reliability statement (item 132). Right as the first link, wrong as
the only one.

**B. A pool of free writers on the small brief.** Gemini, then Groq, then
Gemini Flash-Lite, Mistral, SambaNova, OpenRouter; one attempt each; the floor
last. On the record's Gemini rate and an assumed 0.5 per other link, two
sweeps land a write-up on ~99% of days; if refusals correlate across vendors,
~70% with the floor on the rest. Unmeasured: independence of refusals, and
whether free writers pass the audit on a brief (items 172 and 180 measured
failures on a 100K-character input with thousands of words out; that is not
this job). Cost: the operator holds four or five free accounts; a forker with
no keys gets the floor every day.

**C. A local model on the runner (zero external LLM).** llama-server on the
public-repo runner, a ~2.5 GB Q4 GGUF from actions/cache, six section calls of
~200 tokens each under a JSON grammar, the audit, the floor per section. No
key, no quota, no 503, as many issuances a day as the runner will run.
Estimated 6 to 12 minutes a run; the estimate is inferred from a 4-thread
desktop at 43 tokens/s prompt and 12 tokens/s generation for a 3B model, not
measured on a runner. Prose quality of a 4B model is the unknown; the audit
bounds numbers and shape, not meteorology. GitHub's Actions terms restrict
hosted runners to "the production, testing, deployment, or publication of the
software project associated with the repository" and to burdens proportionate
to benefit; the daily forecast is the repo's publication and ten CPU-minutes
is small. The operator should read the clause before relying on it. A private
fork gets 2 vCPU, 8 GB and 2,000 minutes a month: one run a day, not two.

**D. On-device in the app.** Apple Foundation Models (iPhone 15 Pro and
later; 4,096 tokens shared between input and output; no Swahili), Gemini Nano
(flagship list; foreground-only per the plugin README), or a 0.5 GB Gemma
download on a 4 GB phone at perhaps 30 to 50 s on a flagship and unmeasured on
a mid-range phone. It serves the phones least in need of it. Worth having as
one optional link once the app has the floor; not an architecture.

**E. The reader's own chat app (item 182).** On hold to 2026-11-01 by the
operator's choice. The brief in step 4 is what it would publish, so this route
becomes a by-product of the spine.

**F. Async prose: hourly ticks after the run.** Figures at 03:01Z; then a
`schedule:` job each hour to 12:07Z that exits at once if the day has prose
and otherwise makes one call on the next route with allowance; the mailer
holds the email up to two hours. Bounded at today's 9-call pin. Buys nothing
if a shed morning stays shed until the US wakes; the record has no 04 to 12Z
data. Composes with B (ticks across routes). With C in the chain the ticks
are unnecessary.

**G. Product = the record (brief option 3).** Who-to-trust rows per
variable, the met service scored beside the global models, a published
dataset, a pull-only registry pooling findings by 0.25-degree cell for cold
starts. All code; no key. A product decision rather than a fix for the
write-up: its engineering (steps 1 to 3 above, a record-note composer, a
dataset export) is shared with everything else, and the registry is a later
item on its own.

**The deterministic-only angle (no LLM anywhere).** Precedent is long:
Environment Canada's SCRIBE has produced public forecast text from numbers
since 1995 and still does (Meteocode); SumTime's readers preferred rule text
to forecasters' for the sublanguage it covered (Reiter et al. 2005; Belz
2007); NWS text products are formatter output and only the Area Forecast
Discussion is human. This repo already has content determination and
micro-planning for rain, wind, cloud, temperature change and trend; it has no
realiser. What such text cannot do is say why models disagree. That is the
ceiling of the floor, and it is one sentence per issuance on the record.

## 5. The interpreter: the LLM's real job

The operator's intent, stated 2026-09-30: the LLM was to aid the learning
and to interpret the forecast beyond prose and beyond arithmetic. The
arithmetic sees wind and rain at Day+5; the LLM should see the system
behind them. That is the Area Forecast Discussion role, the one job neither
rules nor arithmetic can do, and nothing in section 3 removes it. Three
facts say it is not happening today.

- **The LLM cannot see the system.** Item 103, raised by the operator on
  2026-09-11, measured the synoptic layer: nine points at plus or minus 12
  degrees, daily means, `best_match` only, three days out, never stored,
  never scored. It cannot tell deepening from approaching, cannot see the
  middle of its own window, and keeps nothing from yesterday. A Day+5 surge
  reaches the model as the same per-model daily numbers the arithmetic
  reads. `prompt.py:236` forbids naming a centred low, correctly at that
  sampling. A confident synoptic story from those inputs is confabulation;
  item 135 exists because the narrative has invented proper nouns.
- **The prompt has removed interpretation one measured rule at a time:** no
  narrated weighting, no ranking from the record, no comparison, no sky
  split by model. Each rule was earned. The classified write-up holds one
  inference sentence in 61, and it is generic.
- **Nothing scores insight.** The LLM's only scored output is its Day+0
  call, where item 72 notes the raw models are near their ceiling, and there
  it trails arithmetic. Item 71, whether the LLM layer is the bottleneck at
  all, has never been run.

So the LLM job splits in three, not two:

1. The code call and the floor (section 3): the forecast, complete every
   day, no LLM on the path.
2. The writer (section 4): a small brief to prose, any route, or the floor.
3. The interpreter: a separate, optional, asynchronous call whose inputs are
   built for the question. Item 103 gives the order: persist the ring; widen
   it (more points, per model, several days, and the wind field, since
   surface pressure carries little near the equator); hand it the per-model
   Day+1 to Day+7 series side by side, where a track disagreement shows as
   two models bringing a system and one keeping it away. Its output is a
   claim, not a paragraph: system, direction, impact window, magnitude,
   models agreeing. The claim is stored and scored when the window closes,
   in the event shape items 62 and 82 sketch. The reader's prose is written
   from the claim, by the writer.

That is how the LLM aids the learning: by making claims the record can
test. If its synoptic claims verify better than the models' own extended
numbers, the accuracy page shows it and the aggregator objection dies on
evidence. If they do not, the page shows that. Today the LLM's insight,
where it exists, is unrecorded and unscored, which is indistinguishable
from absent.

Limits. Kisumu cannot test the case: fifty days of equatorial lake
convection hold no synoptic storm, so the interpreter's value shows first on
a fork with a coast or a mid-latitude climate. Interpretation is the one
call where model quality outweighs route count, so item 71's frontier
reference is worth running as a measurement, in a subprocess handed the
prompt alone, before deciding whether Flash can do the job. Keeping the
write-up and the interpretation in one 56K-token call is the arrangement
that fails together, which is what the record shows.

### Unproven, not unrealistic. The operator's reading, 2026-10-01

The operator's conclusion on reading the above: an LLM forecast discussion
on free tiers is not realistic on a schedule; the forecasting is done by
code, arithmetic and the record, and the discussion is the part that is a
struggle. Half of that the record confirms, and half it does not.

**Confirmed:** a daily discussion from one free vendor, at a fixed minute,
from a 56K-token call, is not reliable. The 03Z write-up landed first try on
8 of 17 mornings and on 1 of 8 since 09-22. No retry schedule fixes a shed
pool.

**Not confirmed:** that the discussion is the part that struggles. What
struggled was delivery of a particular call shape. The content delivered was
not analysis: 57 of 61 sentences in the best write-up were restatement, and
the prompt forbids most of the rest. The analytical job has not been tried,
because the inputs that could support it do not exist (item 103). The
measured statement is narrower: nobody has asked a model to interpret with
inputs that could show a system, and nothing would have scored the answer.

Three things change the odds once the job is reshaped:

- **Small inputs reach strong free reasoners.** The 56K-token call excluded
  every free route but Gemini. An interpreter's inputs are a few thousand
  tokens by nature: the ring's history, the per-model Day+1 to Day+7 series,
  a wind-field summary. At that size gpt-oss-120b on Groq or Cloudflare and
  DeepSeek on SambaNova are reachable for free (section 2), and those are
  reasoning models, not writers.
- **It does not need a schedule.** The discussion's value is concentrated on
  the few days a system is moving. A claim about Day+5 is as useful at 14:00
  as at 06:00, and on most days the right output is "no claim". Patient,
  event-shaped, optional is the correct form for this job, not a compromise.
- **It can be scored.** A claim with a window and a magnitude verifies
  against the station and the reanalysis the record already holds. Then
  "does the LLM add skill" has data at Day+3 to Day+7, where item 72 says
  the raw models have room, instead of at Day+0 where they do not.

So the conclusion holds with one word changed: unproven rather than
unrealistic. The forecast stands on code and the record, which is
demonstrated. The discussion becomes enrichment behind gates (section 6),
which is the reliable part. The interpreter becomes an experiment with a
stop/go: persist the ring, define the claim, run item 71's reference once to
learn whether a Flash-class model can interpret at all. If it cannot, the
claims table will say so and the product has lost nothing. If it can, the
free-tier question is already answered by the input size.

What stays true under either outcome: Kisumu will not produce the test case.
Fifty days of lake convection hold no synoptic system. The interpreter earns
or loses its place on a fork with a coast or a winter.

## 6. Getting the LLM's contributions in reliably

Added 2026-10-01. The system is good because everything in it is computed,
stored and scored. The LLM has been the exception: one large call on the
critical path, gated by a schema alone, its insight unrecorded. Reliability
here means making the LLM's contributions obey the same rules as everything
else. It has two parts the current design conflates: availability (did
anything arrive) and trust (may what arrived enter the record). In build
order:

1. **The run publishes before it asks.** Fetch, verify, code call, tiles,
   floor, rows, commit, publish. Everything after that is enrichment. The
   mailer holds a configurable number of hours for enrichment, then sends
   what exists. No LLM outcome can lose a day, in the pipeline or the app.
2. **Every LLM job reads a stored input, never a rebuilt one.** The writer's
   brief and the interpreter's inputs are rendered by the run, stored on the
   entry, hashed. A later attempt, on any route, at any hour, asks the same
   question; outputs are comparable and the archive holds what each model
   was asked. This removes item 186's blocker and makes retries idempotent.
3. **Each contribution enters through a typed gate.** Three kinds:

   | contribution | gate | on failure | scored how |
   |---|---|---|---|
   | the LLM's own call (a row) | schema; inside the models' spread unless flagged (item 182's rule) | absent row, `None` | as a model, hidden from the forecaster, as today |
   | the interpreter's claim | schema: system, direction from a fixed vocabulary, window inside the horizon, magnitudes with units, models named that exist in the run; a "beyond the models" flag when it exceeds every model's number | absent claim | at window close against station and reanalysis: hit, false alarm, miss |
   | the writer's text | numbers in the brief, locked phrases verbatim, headings, `phrase_defect`, forbidden words | next route, then the floor | audit pass rate per route in the ledger |

   The claim gate is the new one. A claim naming a system no model's number
   carries is allowed, because that is the point, but it is marked, stored
   and scored. Misses are scoreable too: a window where observed gust or
   rain exceeded every extended model by a margin, with no claim, is a miss.
4. **Chains per job, one attempt per link, ordered by evidence.** The
   existing `FallbackProvider` with per-link ceilings, plus two changes: a
   link is chosen per job type, and a failed audit falls through like a
   refusal. Gemini first for both jobs, the best free writer on the record.
   For the writer, a local model on the runner as the last link, since it
   cannot be refused; below it the floor. For the interpreter, quality
   outranks route count, so the chain is shorter and ends in "no claim
   today" rather than a weak model's confabulation. Ordering is re-ranked
   monthly from the ledger's served rate and audit pass rate per route.
5. **Patience, bounded.** An hourly job that exits at once when both jobs
   are done and otherwise spends one call on the next route with allowance,
   until a local closing hour. The writer closes by mid-morning: prose about
   a morning already past is worth less by noon. The interpreter can stay
   open longer: a Day+5 claim is as useful at 14:00 as at 06:00. The 24-hour
   cap stays as the runaway guard.
6. **Provenance everywhere, then the accuracy page.** The ledger gains the
   job type and the audit result; the entry gains who wrote the text and who
   made the claim. The accuracy page gains a claims table beside the model
   rows. That table is where "the LLM aids the learning" becomes visible,
   or does not.
7. **What the reader and the app see.** A page that reads as a forecast at
   06:05 local, a discussion that fills in when served, a claim that appears
   as a hazard line when validated. The app's viewer re-fetches on resume;
   own-key mode runs the same jobs against the user's routes, and their 20
   calls become 20 enrichments rather than 10 forecasts.

Cost on a clean day: two Gemini calls, one per job. On a bad day: two
Gemini calls, whatever the free pool spends, zero for the runner; inside
today's 9-call pin.

What this reverses, said plainly: item 59's two-call split made the scored
call the LLM's and protected it from the narrative. This makes the scored
call code's and demotes the LLM's call to a hidden row. Everything item 59
protected is protected better, and the LLM's row keeps being scored, so
item 173's question keeps being answered.

## 7. A recommended order

Cheapest and most reversible first. Each step stands alone and is worth
having even if nothing after it ships. This is a design for approval, not a
plan to start: the 09-30 decision was to stop build work.

1. Numbers before the LLM; the code blend as the served call; the LLM call
   optional and hidden. Python, vectors, Dart, re-pin. Closes the no-entry
   hole in both repos.
2. The floor. A realiser over the composers that exist: ~700 to 1,100
   characters at first (day-over-day sentence, rain sentence, thunder timing,
   wind shift, sky words, high/low/gust, next three days, Gulf timeline).
   Item 37's day-characters grow it later. Grow it only as far as the
   operator's reading test (item 75) allows.
3. The brief, the writer prompt and the audit. Store the brief on the entry so
   a later write-up reads the same input; this also removes item 186's stated
   blocker for two-step publishing.
4. The writer chain: Gemini once, then whichever of B and C measurement
   picks. C is the only link that cannot be refused; if it passes the reading
   test it belongs last in every chain, before the floor.
5. App: step 1 in olw_core gives a key-free forecast with the floor; the key
   becomes the upgrade; on-device is an optional link after.
6. The interpreter, under item 103's order: persist the ring, widen the
   inputs, define the scored claim, then the call. Optional and
   asynchronous; never on the daily path.
7. Record-first extras, registry, dataset: the operator's product call.

## 8. Measure before building

Each with a stop/go; none needs new code on main.

- Floor readability (zero calls, an hour): render the floor for the 8
  placeholder days and 5 served days; the operator reads them beside the
  09-23 write-up. Go if publishable as "Today's Forecast". If not, the floor
  needs item 37 before anything else.
- Brief size (zero calls): render the brief for the 27 archived issuances in
  `data/prompts/`; count tokens. Go if p95 is at most 4.5K tokens with the
  writer prompt; above 6K, Groq drops out.
- Writer backtest (a laptop, a day): Gemini, Groq and a 4B model via
  llama.cpp on the 27 briefs; audit pass rates; the operator reads three. Go
  for each route at 80% audit pass or better.
- Size-blindness (10 mornings, 2 extra Gemini calls each): the brief and the
  full prompt at the same minute, alternating order. Says whether a small
  call lowers Gemini's own refusals or only opens other routes.
- Runner bench (one sandbox workflow run, zero calls): llama-bench on
  ubuntu-latest for three 3B to 4B models, plus cache-restore time and the
  CPU model. Go at 30 tokens/s prompt and 6 tokens/s generation or better.
- Route independence (14 days, from health_check): send the brief to every
  keyed route at 03:01Z; log outcomes per vendor. Decides whether B's pool is
  worth its accounts.
- Read GitHub's Actions terms against C; record the wording in the roadmap
  item.
- Frontier reference (item 71): a frontier model over the archived days, in
  a subprocess handed the prompt alone; record the dated ceiling. Decides
  whether Flash can carry the interpreter at all.

## 9. Questions only the operator can answer

- Is the product the prose or the verified forecast? The spine is the same
  either way; the answer decides steps 4, 6 and 7.
- Does the LLM's own scored call stay in the daily run when a key exists?
  Hidden, it costs nothing on reliability and keeps item 173's question
  answerable.
- How many free accounts may a deployment depend on: zero (C only), one
  (Gemini plus C), or several (B)?
- Must a code-written write-up be labelled as such on the page? On the
  record's principles, yes; the wording is the operator's.
- Approve the three-job split (section 5) and publish-before-asking (section
  6) as the target. It reverses item 59 in spirit; section 6 says how.
- Is a local model on the public runner acceptable as the guaranteed writer
  link? The Actions clause is quoted in section 4; the read is the operator's.
- The claim's shape and what counts as verified (sections 5 and 6), after
  the ring is persisted: a few hundred bytes a day, the one storage change
  to make before anything else, because the record cannot say anything
  about systems until it remembers yesterday.
- Whether this reopens the 09-30 decision to stop building. If it does, the
  first afternoon is zero-call: render the floor and the brief over the
  archive and read them. Nothing else should move until those two pages
  have been read.
- The Meteorology Act question (item 182) is unchanged by any of this and is
  larger than all of it.

## 10. Found on the way, not chased

- `meta.input_tokens` on the entry is one call's usage, not both:
  `_generate_forecast` returns the combined meta (`_call_meta`,
  `pipeline.py:4030`) and nothing reads it; the entry reads the
  last-write-wins holder (`pipeline.py:2888`). Items 112 and 148's cost
  series mix the two conventions.
- `describe_day_over_day` (item 164) is a finished, vectored sentence with no
  reader. It is the first line of any floor.
- The app discards the extraction it already computed when the judgment call
  fails.

## 11. What was not checked

- No writer route was called; every quality claim about a small brief rests
  on item 182's Haiku backtest, which was a full forecaster, not a writer.
- No model was run on a GitHub runner; throughput is inferred.
- Only one served write-up was classified sentence by sentence.
- Mistral's terms were not read; SambaNova's card requirement is unresolved;
  Gemini per-model RPD beyond 3.8 Flash is unverified.
- The 2-minute pause and the +1h second chance have no runs of evidence.
- Run and phase classification in the ledger analysis is heuristic (runs are
  clusters with gaps of at most 31 minutes; the judgment is taken as the
  first call).
