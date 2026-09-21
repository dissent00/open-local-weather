"""Is a composed phrase shaped like something a reader can be handed?

ROADMAP item 158, raised 2026-09-21 out of a defect that reached two
published forecasts.

WHY THIS EXISTS AT ALL. Item 158's design is that code composes the Overview's
sentences and the prompt orders the model to use them VERBATIM. That is the
right trade — a composed sentence cannot be re-welded, re-ordered or widened,
which is what the model kept doing to fragments — but it moves the whole
burden of correctness onto the composer, because nothing stands between the
composer's output and the reader. On 2026-09-20 and 09-21 `_join_tails`
emitted "much the same through Thursday, with , and showers and thunderstorms
likely each day" and the model published it, faithfully, as instructed.

WHY THE VECTORS DID NOT CATCH IT. `spec/export_vectors.py` computes each
`expected` by CALLING the Python function, so `extended_trend.json` held the
broken string as its pinned answer. The Dart mirror matched it exactly and the
suite was green. A golden vector proves the two languages agree and that
nothing changed by accident; it cannot say the answer was right. Nine
mutations for item 158 step 2 each bit their own case and none could have
found this, because a mutation test asks whether a guard notices a change, not
whether the pinned answer was ever correct.

So this is the check a golden vector structurally cannot be: a claim about the
SHAPE of a phrase that is true independently of what the composer produced.
The exporter refuses to write a phrase that fails it, which is where this
defect would have been caught, and the pipeline drops one rather than printing
it, which covers the inputs no vector case reaches.

WHAT IT DOES NOT DO. It cannot tell whether a phrase is TRUE, whether it names
the right day, or whether it reads well. It catches the join artefacts — the
empty item, the doubled space, the dangling conjunction — which is the class
that arises from composing text out of lists, and that is the class that shipped.
"""

# The marks a phrase may not carry a space in front of. A composed phrase is a
# sentence fragment, so its punctuation is the ordinary kind; a space before
# one is a list that was joined with an empty item in it, which is exactly the
# shape the 2026-09-20 defect took (", and" built onto "").
_SPACE_BEFORE = (" ,", " .", " ;", " :", " !", " ?")

# A phrase that stops on a joining word stopped in the middle. Checked with a
# leading space so "background" does not read as a trailing "and".
_UNFINISHED_ENDINGS = (",", ";", ":", " and", " or", " with", " but", " from")

# A phrase that STARTS on one began in the middle. The composers all produce
# fragments that open on their own subject.
_UNFINISHED_OPENINGS = (",", ";", ":", "and ", "or ", "but ", "with ")


def phrase_defect(text: str | None) -> str | None:
    """The reason `text` is not shaped like a finished phrase, or None.

    None IN IS NONE OUT, and that is not an oversight. Every composer here
    returns None for a legitimate absence — the models share no bearing, the
    comparison found nothing worth a sentence — and the prompt renders that as
    the block being absent. An absence is the designed answer; only a PRESENT
    string can be malformed.

    The reason is returned rather than raised so each caller can choose: the
    vector exporter raises on it, because a malformed phrase there is a defect
    being pinned as an answer, and the pipeline drops the phrase and records a
    degradation, because a live run publishing nothing beats a live run
    publishing punctuation.
    """
    if text is None:
        return None

    if not text.strip():
        return "empty"

    if "  " in text:
        return "doubled space"

    for mark in _SPACE_BEFORE:
        if mark in text:
            return f"space before '{mark.strip()}'"

    # ", ," and ",," are a list joined with an empty item still in it. The
    # space-before check above already catches ", ," — this one is for the
    # unspaced form and for the message it gives, which names the cause.
    if ",," in text:
        return "empty item in a list"

    stripped = text.rstrip()
    for ending in _UNFINISHED_ENDINGS:
        if stripped.endswith(ending):
            return f"ends on '{ending.strip()}'"

    lowered = text.lstrip().lower()
    for opening in _UNFINISHED_OPENINGS:
        if lowered.startswith(opening):
            return f"starts on '{opening.strip()}'"

    return None
