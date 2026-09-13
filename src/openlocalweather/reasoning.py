"""Does this issuance earn an LLM call?

ROADMAP item 121, and the half of item 120 that survived it. The pipeline's
first principle is that facts are composed in code and never asked of the
model; item 121 applied it to observations, which removed the commonest
reason a frequent refresh had to spend anything at all. What is left is this
question, and it is the operator's:

    "So I could run cron every hour, I'd get sensor updates from code as
    we're now discussing, and only hit my LLM API if there's new model data."

THE SIGNALS ARE NOT NEW AND ARE NOT COMPUTED HERE. `InformationMoved` has
recorded all three of C2's triggers on every entry since item 104 stage 2b,
deliberately acted on by nothing, so that the record could show how often
each fires against real weather before anything was decided on them. This
module is the deciding, and it reads that record's own shape rather than
recomputing it — the signal that gets stored and the signal that gets acted
on must not be able to drift apart.

WHY A POLICY RATHER THAN A CONSTANT. C2 reasoned that a stale DAYPART earns a
narrative call: a forecast written at 06:00 does go stale as a document by
evening, and a reader arriving once, at dusk, sees that staleness. That
reasoning stands. What item 121 changed is the COST side — it is now the only
automatic spend that buys no new information — and some deployments will not
want it while others will. Item 120's answer is to declare it on both sides,
OLW's location.yaml and the app's settings, rather than bake it in.
"""

from __future__ import annotations

from enum import StrEnum

from openlocalweather.models import InformationMoved


class LLMRefreshPolicy(StrEnum):
    """When a LATER issuance of a day may spend an LLM call.

    A day's first issuance is outside this entirely — see `llm_should_reason`.
    """

    # Every issuance re-reasons, which is what every run did before item 121.
    # The daypart narrative is refreshed whether or not anything moved.
    ALWAYS = "always"

    # Chase model runs only. Observations still refresh every run, in code and
    # for free; the prose stays the prose of the last real forecast.
    NEW_CYCLE_ONLY = "new_cycle_only"

    # Adds C2's third trigger: an observation that CONTRADICTS the standing
    # call also earns a re-forecast.
    #
    # NOT THE DEFAULT, AND THE REASON IS A MEASUREMENT THAT HAS NOT BEEN
    # TAKEN. The contradiction test is sound, but its temperature margin
    # (`disagreement.TEMP_CONTRADICTION_MARGIN_C`) is commented "CONSERVATIVE
    # AND NOT YET MEASURED" — it needs observed station highs through the day
    # set against the standing call, which the record cannot supply until more
    # later issuances exist. Defaulting to it would ship an unmeasured
    # threshold into a spending decision, which is the mistake ROADMAP item
    # 100 costed: a bound sized from two convenient samples landed 45
    # characters from refusing legitimate output.
    #
    # Item 121 also argues the contradiction is reported in code EITHER WAY,
    # and that the code line is the more reliable of the two — an LLM
    # re-forecast may not mention it, which is what the prompt's `left_out`
    # rule exists for. So this buys a re-reasoning, not the reader's
    # knowledge of the contradiction.
    NEW_CYCLE_OR_CONTRADICTION = "new_cycle_or_contradiction"


def llm_should_reason(moved: InformationMoved, policy: LLMRefreshPolicy) -> bool:
    """Whether this issuance re-reasons, or refreshes its observations and
    stops.

    Returning False does not mean the run does nothing: it fetches, composes
    what the station has seen, and re-renders. It means only that no judgment
    and no narrative are bought.
    """
    # The day has no forecast at all yet, so there is nothing to preserve and
    # nothing to compare against. This is C2's first trigger and it is not
    # subject to policy — a deployment that switched it off would publish a
    # day with observations and no forecast.
    if moved.first_issuance_of_day:
        return True

    # `==`, NOT `is`. LLMRefreshPolicy is a StrEnum and the value can reach
    # here as a plain string — `model_copy(update=...)` skips validation, and
    # so does any caller that builds a LocationConfig without going through
    # `load_location_config`. Identity comparison fails silently on those and
    # falls through to the cheap path, which is a spending decision made by
    # accident. String equality is what StrEnum is for and is correct for both.
    if policy == LLMRefreshPolicy.ALWAYS:
        return True

    # THREE-VALUED, AND None IS NOT False. `guidance_is_newer` is None when
    # there was no BASIS for the comparison — an entry written before the
    # cycle was recorded, or a run that fell back to the derived floor while
    # the previous one had a real observation, which means this run knows LESS
    # than its predecessor did. See `_guidance_recency_payload`.
    #
    # No basis resolves toward spending. Taking the cheap path is an
    # optimisation and an optimisation needs positive evidence that nothing
    # moved; without it, do what every run did before this gate existed. That
    # also keeps entries written before `guidance_initialised_at` existed from
    # silently losing their narrative refreshes.
    if moved.guidance_is_newer is not False:
        return True

    if policy == LLMRefreshPolicy.NEW_CYCLE_OR_CONTRADICTION and moved.observation_disagreements:
        return True

    return False
