"""Markdown narrative -> HTML, sanitised. One function, two call sites.

WHY THIS IS NOT INLINE IN EITHER CALLER. The page and the email each did
their own `markdown.markdown(...)` and the two were free to drift; when the
sanitising was added there would have been two places to remember. They now
share this, which is the only way a rule like "the narrative is sanitised"
can be true of the system rather than of one file.

WHY IT IS NEEDED AT ALL, given autoescape. Autoescape protects every other
value on the page. It cannot protect this one, because this one is HTML on
purpose: the template marks it `| safe` so its `<h2>` renders as a heading
rather than as text. Whatever else the model writes renders too.

`markdown.markdown` passes embedded HTML through untouched — Python-Markdown
dropped `safe_mode` in 3.0 and its documentation is explicit that sanitising
is the caller's job. Verified on 2026-09-10 rather than assumed:
`<script>alert(1)</script>` in a narrative survived the conversion intact,
reached `docs/index.html`, and was mailed to subscribers. Nothing had ever
looked at it.

WHY nh3. `bleach` is archived and its own README points here. nh3 wraps
ammonia, which parses the HTML properly rather than matching patterns — the
distinction that matters, because a regex sanitiser is a long argument with
an adversary that has read the same regex.

THE DEFAULT ALLOWLIST IS DELIBERATE, not laziness. It already covers
everything this narrative produces — measured across the stored record, the
only tags are h2, h3 and p, and `test_the_real_stored_narrative_is_unchanged_by_sanitising`
pins that no published forecast renders differently. A hand-written list here
would be a second thing to maintain and a first thing to get wrong, and it
would silently drop a tag the day the prompt starts asking for a table.
"""

import markdown
import nh3

_EXTENSIONS = ["extra"]


def narrative_to_html(narrative_markdown: str) -> str:
    """The forecast narrative as HTML safe to put on a page or in an email."""
    return nh3.clean(markdown.markdown(narrative_markdown, extensions=_EXTENSIONS))
