"""Does a BACKGROUND submit get accepted when a synchronous generation is refused?

ROADMAP items 132 and 80. The record says our failures are refusals, not hangs
— 25 x HTTP 503 against 2 timeouts — and item 80 already concluded that
submit-and-poll cannot help with a refusal, because "a submit has to be
accepted before there is anything to poll".

THAT CONCLUSION HAS AN UNTESTED ASSUMPTION IN IT: that a submit and a
generation are equally acceptable to a loaded service. They may not be. A
submit that only ENQUEUES work is a cheap thing to accept, and the Interactions
API enumerates `queued` as an interaction status — the state of work taken on
but not yet started. If the free-tier pool sheds generations while still
accepting queued jobs, the answer to item 132 changes completely.

This probe tests exactly that, and nothing else.

### The design, and why each part of it is the way it is

**PAIRED, because the pool changes second to second.** Measured 2026-09-15:
a call was refused at 03:01:50, accepted at 03:02:28 and refused again at
03:02:55. A background submit that succeeds on its own proves nothing; it has
to succeed while a synchronous call taken seconds earlier failed. So each
trial is sync-then-submit, back to back.

**THE REAL PROMPT, not a toy one.** If shedding is at all sensitive to request
size, a small probe would be accepted where a 35,000-token forecast is not,
and would report a false positive. This reads the newest archived user prompt
so the probe weighs what a forecast weighs.

**IT ONLY ASKS ABOUT ACCEPTANCE.** No response schema is sent. Whether
`response_format` works under `background` is a real question and a separate
one; mixing it in here risks a 400 that muddies the only result this is for.

**IT SPENDS, AND IT SAYS SO FIRST.** Every request counts against the
provider's RPD of 20, including refusals — established from the operator's
dashboard on 2026-09-15. Polls are requests too and may also count; whether
they do is one of the things this run will reveal. Dry-run is the default and
`--yes` is required to send anything.

**RUN IT DURING AN EPISODE.** Outside one, both legs succeed and the trial is
void — the script says so rather than reporting a result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
INTERACTION_URL = "https://generativelanguage.googleapis.com/v1beta/interactions/{id}"

# Terminal states, from the Interactions API reference. `queued` and
# `in_progress` are the two that mean "accepted, still working" — which is the
# whole point of the probe.
TERMINAL = {"completed", "failed", "cancelled", "incomplete", "budget_exceeded"}

REQUEST_TIMEOUT_S = 90
POLL_AT_S = (20, 60, 180)


def _shape(value, depth: int = 0):
    """The structure of a response, without its contents.

    What a provider implementation needs is which KEYS exist and what types
    they hold. What it does not need, and what must not land in a file, is the
    generated text or anything echoed back from the prompt — so strings are
    reported by length unless they are short enough to be a status or an id.
    """
    if depth > 6:
        return "<deeper>"
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1), f"<list of {len(value)}>"] if value else []
    if isinstance(value, str):
        return value if len(value) <= 64 else f"<str len {len(value)}>"
    return type(value).__name__

def newest_prompt(data_dir: Path) -> tuple[str, str]:
    """The most recent archived user prompt, so the probe weighs what a
    forecast weighs."""
    files = sorted((data_dir / "prompts").glob("*.json"))
    if not files:
        raise SystemExit("no archived prompts found; this probe needs a real one")
    issuances = json.loads(files[-1].read_text())["issuances"]
    return files[-1].stem, issuances[0]["user_prompt"]


def sync_call(key: str, model: str, prompt: str) -> dict:
    started = time.monotonic()
    try:
        r = requests.post(
            GENERATE_URL.format(model=model),
            params={"key": key},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
            timeout=REQUEST_TIMEOUT_S,
        )
        return {"leg": "sync", "status": r.status_code, "elapsed_s": round(time.monotonic() - started, 3)}
    except Exception as e:  # noqa: BLE001 - a probe reports, it does not raise
        return {"leg": "sync", "status": None, "error": str(e),
                "elapsed_s": round(time.monotonic() - started, 3)}


def background_submit(key: str, model: str, prompt: str) -> dict:
    started = time.monotonic()
    # `store` is left at its default: item 80 recorded that store=false is
    # incompatible with background=true.
    try:
        r = requests.post(
            INTERACTIONS_URL,
            params={"key": key},
            json={"model": model, "input": prompt, "background": True},
            timeout=REQUEST_TIMEOUT_S,
        )
        out = {"leg": "background_submit", "status": r.status_code,
               "elapsed_s": round(time.monotonic() - started, 3)}
        try:
            body = r.json()
            out["interaction_id"] = body.get("id") or body.get("name")
            out["interaction_status"] = body.get("status")
            # THE WHOLE ENVELOPE, because building a provider against a guessed
            # response shape is how requests get burned discovering it. The API
            # reference gives the REQUEST fields and not where generated text
            # lands, so one accepted submit is worth more than any amount of
            # reading. Kept shallow — keys and types, with only short strings
            # verbatim — so a prompt echo or a key cannot end up in a file that
            # gets committed.
            out["envelope"] = _shape(body)
        except ValueError:
            out["body_snippet"] = r.text[:400]
        return out
    except Exception as e:  # noqa: BLE001
        return {"leg": "background_submit", "status": None, "error": str(e),
                "elapsed_s": round(time.monotonic() - started, 3)}


def poll(key: str, interaction_id: str) -> list[dict]:
    seen = []
    for wait in POLL_AT_S:
        time.sleep(wait)
        started = time.monotonic()
        try:
            r = requests.get(
                INTERACTION_URL.format(id=interaction_id),
                params={"key": key}, timeout=REQUEST_TIMEOUT_S,
            )
            status = None
            try:
                status = r.json().get("status")
            except ValueError:
                pass
            seen.append({"after_s": wait, "http": r.status_code, "status": status,
                         "elapsed_s": round(time.monotonic() - started, 3)})
            if status in TERMINAL:
                break
        except Exception as e:  # noqa: BLE001
            seen.append({"after_s": wait, "http": None, "error": str(e)})
            break
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--model", default=os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash")
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--envelope-only", action="store_true",
                    help="ONE background submit, no synchronous leg, and poll it to a "
                         "terminal state. This answers a DIFFERENT question from the rest "
                         "of the probe and needs no episode: where does the generated text "
                         "land in an Interactions response. That shape is what blocks "
                         "writing a provider, and waiting for an outage to learn it would "
                         "be waiting for the wrong thing.")
    ap.add_argument("--poll", action="store_true",
                    help="also follow each accepted submit to a terminal state. OFF by "
                         "default: ACCEPTANCE is the discriminator this probe exists for, "
                         "and polls are requests against the same 20-a-day ceiling. The "
                         "interaction id is recorded either way, so completion can be "
                         "checked later, by hand, outside the episode and for one request.")
    ap.add_argument("--out", default="")
    ap.add_argument("--yes", action="store_true",
                    help="actually send. Without it nothing leaves the machine.")
    a = ap.parse_args()

    day, prompt = newest_prompt(Path(a.data_dir))
    if a.envelope_only:
        a.trials, a.poll = 1, True
    per_trial = (1 if a.envelope_only else 2) + (len(POLL_AT_S) if a.poll else 0)
    print(f"prompt: {day}'s archived user message, {len(prompt):,} chars (~{len(prompt)//4:,} tokens)")
    print(f"model:  {a.model}")
    if a.envelope_only:
        print(f"mode:   ENVELOPE ONLY — 1 submit + up to {len(POLL_AT_S)} polls, no sync leg.")
        print("        Learns where generated text lands. Needs no episode.")
    else:
        polls_note = (f" + up to {a.trials*len(POLL_AT_S)} polls" if a.poll
                      else "; polling off, pass --poll to follow each job to a terminal state")
        print(f"trials: {a.trials}  -> up to {a.trials * per_trial} requests "
              f"({a.trials} sync + {a.trials} submit{polls_note})")
    print("\nEVERY ONE OF THOSE COUNTS AGAINST THE PROVIDER'S DAILY LIMIT, refusals")
    print("included. The limit measured on 2026-09-15 was 20 a day, and a normal")
    print("forecast day already spends 4.\n")
    if a.envelope_only:
        print("Run this ANY TIME. A successful submit is what teaches the shape, so a")
        print("healthy provider is the good case here rather than a void one.\n")
    else:
        print("Run this DURING an episode. If both legs succeed there was no episode")
        print("and the trial says nothing.\n")

    if not a.yes:
        print("DRY RUN — nothing sent. Re-run with --yes to spend.")
        print(f"\n  POST {INTERACTIONS_URL}")
        print("  " + json.dumps({"model": a.model, "input": "<the archived prompt>",
                                 "background": True}, indent=2).replace("\n", "\n  "))
        return 0

    key = os.environ.get("GEMINI_API_KEY") or ""
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set")

    trials = []
    for n in range(1, a.trials + 1):
        print(f"--- trial {n} ---", flush=True)
        s = {"leg": "sync", "skipped": "envelope-only"} if a.envelope_only else sync_call(
            key, a.model, prompt)
        if not a.envelope_only:
            print(f"  sync:   HTTP {s.get('status')}  {s.get('elapsed_s')}s", flush=True)
        b = background_submit(key, a.model, prompt)
        print(f"  submit: HTTP {b.get('status')}  {b.get('elapsed_s')}s  "
              f"status={b.get('interaction_status')}", flush=True)
        polls = []
        if a.poll and b.get("interaction_id"):
            polls = poll(key, b["interaction_id"])
            for p in polls:
                print(f"  poll +{p.get('after_s')}s: HTTP {p.get('http')} status={p.get('status')}",
                      flush=True)
        if b.get("interaction_id") and not a.poll:
            print(f"  id:     {b['interaction_id']}  (check it later with a single GET)")
        trials.append({"trial": n, "sync": s, "submit": b, "polls": polls,
                       "at": datetime.now(timezone.utc).isoformat()})

    # THE ONLY READING THAT MEANS ANYTHING is a pair where the two legs differ.
    if a.envelope_only:
        env = trials[0]["submit"].get("envelope")
        print("\n=== response envelope ===")
        print(json.dumps(env, indent=2) if env else "  (no JSON body returned)")
        for pl in trials[0]["polls"]:
            print(f"  poll +{pl.get('after_s')}s: status={pl.get('status')}")
        out = Path(a.out or f"probe_envelope_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
        out.write_text(json.dumps({"model": a.model, "trials": trials}, indent=2))
        print(f"\nwrote {out}")
        print("\nNOTE: these requests are NOT in data/spend_ledger.json — this is a")
        print("diagnostic tool, not the pipeline. Reconcile against the provider's")
        print("dashboard rather than against our own count.")
        return 0

    informative = [t for t in trials if t["sync"].get("status") != 200]
    both_fine = [t for t in trials if t["sync"].get("status") == 200]
    print("\n=== reading ===")
    if not informative:
        print("  VOID: every synchronous call succeeded, so no episode was happening.")
    for t in informative:
        sub = t["submit"].get("status")
        verdict = ("SUBMIT ACCEPTED while sync was refused — the hypothesis survives"
                   if sub == 200 else "submit refused too — the hypothesis is dead for this episode")
        print(f"  trial {t['trial']}: sync {t['sync'].get('status')}, submit {sub} -> {verdict}")
    if both_fine and informative:
        print(f"  ({len(both_fine)} trial(s) void — sync succeeded)")

    out = Path(a.out or f"probe_background_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    out.write_text(json.dumps({"model": a.model, "prompt_day": day,
                               "prompt_chars": len(prompt), "trials": trials}, indent=2))
    print(f"\nwrote {out}")
    print("\nNOTE: these requests are NOT in data/spend_ledger.json — this is a")
    print("diagnostic tool, not the pipeline. Reconcile against the provider's")
    print("dashboard rather than against our own count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
