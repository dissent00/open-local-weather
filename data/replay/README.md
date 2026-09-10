# Stored replays

`olw replay --out DIR` sends the frozen prompt vectors through the real model
and keeps the outputs. `olw replay-diff` compares two of these directories and
reports what moved.

**A replay is only worth its cost if it is kept.** The 2026-09-03 attempt spent
five calls and produced no comparison; this directory exists so the next prompt
change has something to diff against instead of spending twice.

One subdirectory per run, named for the date. Each is the model's answer to a
fixed input, so what a diff between two of them shows is the effect of whatever
changed in between — the prompt, the model version, or both. Record which, in
the roadmap, or a later reader cannot tell those apart.

| run | prompt at | why |
|---|---|---|
| `2026-09-10/` | `ea80e73` | First successful replay, and the baseline for ROADMAP item 59's split. Captured after the day's three prompt fixes had settled. |
