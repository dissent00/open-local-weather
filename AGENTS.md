# AGENTS.md

Defaults for agents working in this repo. Adapted from
[Fabien Sanglard's agent.md](https://fabiensanglard.net/agent.md/agent.md),
with one deliberate carve-out (see *Comments*).

Where these conflict with a rule stated in the repo's own docs
(`spec/README.md`, `docs-internal/ARCHITECTURE.md`), those win.

## Prose

Use as few words as possible in commit messages, docs, and replies. Pick
words to cut volume. No superlatives, no praise. Give the cold truth.

## Comments — carve-out

The general brevity rule does **not** apply to comments recording *why*.

This codebase has two implementations held together by shared vectors, and
its comments carry measured findings that prevent re-introducing bugs:
`pick_series` on all-null series, half-to-even vs half-away-from-zero
rounding, `SCHEDULE_EXACT_ALARM` failing silently. `spec/README.md` and
`docs-internal/ROADMAP.md` cite them.

So:

- Explaining a hazard, a measured result, or a non-obvious decision — write
  what it takes.
- Routine comments — one line.
- Never add a comment to code you did not write or change.

## Code

- Extract recurring or meaningful values into named constants. Spec-derived
  values (HTTP 200) get a constant regardless. Self-explanatory one-offs stay
  inline.
- Early return and `continue` over nesting.
- Blank lines between logical blocks.
- Always `{}`, including one-line `if`.
- Keep members private unless the design requires otherwise. Ask before
  widening visibility.
- Program to levels of abstraction. Raw I/O, parsing, and sockets belong in a
  driver layer exposing domain concepts.
- Each layer talks only to the one directly below. No punching through.
- Touch only what the change requires. Minimise changed lines.

## Soft rules

Apply judgement. If you see a case for these, ask before doing it.

- **Enums instead of boolean parameters.** The rule targets languages where
  `f(x, true)` is possible. Python keyword arguments and Dart named parameters
  already make call sites self-describing, so the win is usually small. Worth
  raising when the boolean is hiding more than two states.
- **Function names under 30 characters.** Clarity beats the count.
  `extract_day_n_predictions_from_daily` is 36 and says what it does. Raise it
  when a long name signals the function is doing too much.

## Commits

1. Blank line between subject and body.
2. Subject ≤ 50 chars (72 hard limit).
3. Capitalise the subject.
4. No trailing period.
5. Imperative mood — "If applied, this commit will *[subject]*".
6. Wrap body at 72 chars.
7. Body explains what and why. The code explains how.

## Delegation

A session that does the work itself fills its own context with the work.
Long sessions then lose the thread of WHY, which is the part that is
expensive to rebuild and the part this repo's comments exist to preserve.

So the session you are talking to is a **controller**. It classifies a task,
spawns a worker to do it, and reviews what comes back. It keeps diagnosis,
design, review, and every commit and push for itself.

| Task | Worker |
|---|---|
| Simple, mechanical — a rename, a doc edit, a known one-line fix | Haiku |
| Normal coding — a specified change with tests | Sonnet |
| Difficult or ambiguous — design, unclear cause, cross-cutting | Opus |

**Classify by the work that remains, not by the work as it arrived.** A
failure that looked ambiguous is a Sonnet task once the controller has
diagnosed it and written the specification. A one-line change is an Opus task
if nobody yet knows which line.

The controller does not delegate: triage of a live failure, the decision about
what the fix should be, reviewing the diff, or committing and pushing.

**Delegation does not save tokens; it moves them.** Measured on the first use,
2026-08-28: a two-file workflow fix cost the worker ~59k tokens to rediscover
a repo it started cold in, against maybe 20k had the controller done it
inline. What it bought was controller context — roughly 6k spent on the brief,
the summary and the review, instead of 20k. Total consumption went UP; the
scarce resource went DOWN.

So delegate work that is mostly READING, or mostly waiting: a search across
many files, a change whose verification loop is long, an implementation whose
shape is already specified. Do it inline when the brief would be longer than
the change, or when reviewing it properly means re-deriving the context
anyway — writing a design document is the clearest case, since the brief for
one is the document.

Review is not the place to economise. On that same first use the worker's
report described behaviour the shell did not have (a `git pull` failing inside
`set -e` does not fall through to the next retry, it ends the step). Nothing
shipped wrong, because the diff was read.

**A worker brief is a specification, not a hint.** It carries the files, the
constraint, the tests that must pass, and the rules above that apply — a
worker starts cold and has read none of this conversation. It ends with what
to report: a summary, the diff, and test output.

**Review what comes back before it is committed.** Read the diff, not the
summary. A worker that says a test passes and a test that passes are
different claims, and only one of them is checkable.

## Bugs

Write the failing test first. Watch it fail. Then fix. Then watch it pass.
