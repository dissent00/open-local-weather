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

## Working alone, and clearing on purpose

Handing the work to sub-sessions was tried for a day and measured. It moves
tokens rather than saving them: ~715k spent in workers to keep ~140k out of
this session, about five times the total for the same changes. What it bought
— an uncluttered controller context — is bought more cheaply by writing the
WHY down as you go, which this repo already requires. So do the work here.

Clear the session deliberately, at a seam: a shipped change, a closed roadmap
item, a finished investigation. Not when the context overflows, because by
then the thread being lost is the one you needed. Before clearing, the
reasoning belongs in `docs-internal/ROADMAP.md` or in a comment beside the code. Those
are what a cold session reads, and they are the only part that survives.

Two things are still worth handing out, both because they are mostly READING
or mostly WAITING rather than thinking: a search whose answer is one line but
whose method is opening fifty files, and a verification loop long enough that
you would otherwise sit idle. Point either at prior art in this repo before
the outside world — measured 2026-08-28, a search sent straight outside spent
~98k tokens rediscovering endpoints `docs-internal/ROADMAP.md` already documented.

## Do not trust your own diff

Code someone else wrote gets audited; code you wrote yourself gets believed.
That asymmetry is the expensive one, because the tests that ship with a
change are written by whoever misunderstood the problem.

Before committing:

- **Read the diff as a separate act**, after writing rather than during.
  Three defects were caught this way on 2026-08-28 — a timestamp that stamped
  every issuance of a day with the first one's clock, a docstring claiming
  more than its function delivered, and a rounding divergence — and none
  would have been caught by the tests that came with them.
- **Sweep any arithmetic that crosses the Python/Dart boundary.** Vectors pin
  the cases you chose, not the function: a Dart rounding passed every vector
  case and still disagreed with Python on 962 of 4801 swept values. Generating
  a few thousand inputs and diffing the two outputs takes one command.
- **Run the thing, not only its tests.** A suite of mocks proves the wiring,
  not the behaviour. Driving the real path is what showed that a re-issue
  archived every issuance under the first one's timestamp, while the suite
  was green.
- **Verify a claim before writing it as a comment.** "Measured", "verified"
  and "confirmed" are load-bearing words here; a comment that carries one
  falsely is worse than no comment, because the next person will not re-check
  it.
- **Say what you did not check.** Naming the gap costs a sentence, and it is
  the difference between a report and a claim.

## Bugs

Write the failing test first. Watch it fail. Then fix. Then watch it pass.
