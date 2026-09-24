"""The prompt-provider model's files — ROADMAP item 182.

The operator publishes each location's DATA at a URL; a reader runs it in
their own chat app, with the instructions saved once in a Claude Project or a
custom GPT. This tool writes what gets published.

`probe` comes first because every design here is sized by one number nobody
has: how much fetched text a chat app actually holds. Free ChatGPT's pricing
page, read 2026-09-24, gives only "27K" total context and "~12 pages of text"
of input for Instant, and "Varies" for its reasoning model. Claude chat was
measured cutting the same file near 119,700 characters. A package sized from a
page count would be a threshold set from an estimate, which is what item 100
of this roadmap records going wrong.

THE PROBE IS REAL PROMPT TEXT, not filler. Tokens per character depend on the
content — this prompt is dense with numbers, tabs and repeated keys — so a
ceiling measured on lorem ipsum would not transfer.

CHECKPOINT CODE WORDS ARE WHAT MAKE IT A TEST OF THE CONTEXT, not of the
fetch. ChatGPT reported the 169,138-character archive's length exactly, but it
counted with its code tool, which proves the bytes arrived and not that the
model could read them. A code word cannot be counted or guessed, only seen, so
the last one a model can quote is the last text it holds.

Usage:
  python tools/prompt_provider.py probe <date> <out_file> [chars] [every]
"""
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE_CHARS = 120_000
CHECKPOINT_EVERY = 4_000
MARKER_HASH_CHARS = 12

# Words that cannot occur in a weather prompt, so a quoted one was seen and
# not pattern-matched. Checked against the source text on every run.
CODE_WORDS = [
    "okapi", "tungsten", "marzipan", "glockenspiel", "quokka", "saffron",
    "obsidian", "kazoo", "narwhal", "paprika", "zeppelin", "bergamot",
    "axolotl", "cobalt", "harpsichord", "pangolin", "vermilion", "tamarind",
    "sextant", "lichen", "gondola", "ptarmigan", "cardamom", "bassoon",
    "wombat", "quartz", "trebuchet", "persimmon", "ocarina", "capybara",
]


def end_marker(text: str, label: str) -> str:
    """The last line a reader's model must quote to prove the tail arrived.

    It carries the length and a digest of everything before it, so a model
    with a code tool can verify the whole, and one without can at least
    prove it saw the end.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:MARKER_HASH_CHARS]
    return f"OLW-DATA-END {label} chars={len(text)} sha256={digest}"


def build_probe(
    source: str, seed: str, chars: int = PROBE_CHARS, every: int = CHECKPOINT_EVERY
) -> tuple[str, list[tuple[int, int, str]]]:
    """Real prompt text cut to `chars`, with a checkpoint line after every
    `every` characters, placed at the next line break so no data line is
    split. Returns the text and (number, offset, word) per checkpoint.
    """
    for word in CODE_WORDS:
        if word in source.lower():
            raise SystemExit(f"code word {word!r} occurs in the source text")

    count = chars // every
    if count > len(CODE_WORDS):
        raise SystemExit("more checkpoints than code words")

    words = CODE_WORDS[:]
    random.Random(seed).shuffle(words)

    out: list[str] = []
    key: list[tuple[int, int, str]] = []
    written = 0
    cut = 0
    for n in range(1, count + 1):
        target = n * every
        end = source.find("\n", target)
        if end == -1:
            raise SystemExit("source text shorter than the probe")

        out.append(source[cut:end + 1])
        written += end + 1 - cut
        cut = end + 1

        line = f"=== CHECKPOINT {n:02d}/{count} · after {written:,} characters · code word: {words[n - 1]} ===\n"
        key.append((n, written, words[n - 1]))
        out.append(line)
        written += len(line)

    body = "".join(out)
    return body + end_marker(body, "probe") + "\n", key


def _probe(day: str, out_file: Path, chars: int, every: int) -> None:
    issuances = json.loads((ROOT / f"data/prompts/{day}.json").read_text(encoding="utf-8"))["issuances"]

    # One issuance is ~80K characters since items 174 and 176, short of the
    # probe, so the day's issuances are joined. Repeated blocks are fine: the
    # test is how far a model can see, not what it makes of the content.
    source = "\n".join(i["user_prompt"] for i in issuances)
    # Seeded by the file name too, so two probes of one day do not share an
    # order. They share words, and a position can coincide by chance —
    # CHECKPOINT 02 is persimmon in both of the first two — so each must be
    # run in a chat that cannot recall the others. The first probe.txt
    # (7d68894) was seeded with the bare date and no longer regenerates byte
    # for byte; the committed file is its own answer key.
    text, key = build_probe(source, seed=f"{day}/{out_file.name}", chars=chars, every=every)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(text, encoding="utf-8")

    print(f"{out_file}: {len(text):,} characters, {len(key)} checkpoints")
    for n, offset, word in key:
        print(f"  {n:02d}  {offset:>8,}  {word}")
    print(f"  end  {text.splitlines()[-1]}")


def main(argv: list[str]) -> int:
    if argv[:1] == ["probe"] and len(argv) in (3, 5):
        sizes = [int(a) for a in argv[3:]] or [PROBE_CHARS, CHECKPOINT_EVERY]
        _probe(argv[1], Path(argv[2]), *sizes)
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
