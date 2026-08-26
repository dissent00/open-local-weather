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

## Bugs

Write the failing test first. Watch it fail. Then fix. Then watch it pass.
