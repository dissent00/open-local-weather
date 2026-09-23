"""The fallback chain — ROADMAP item 81.

What these pin is the one judgement the chain makes: whether the failure in
front of it is a vendor that is down or a model that answered badly. Getting
that wrong in either direction is worse than having no chain. Falling back on
a schema failure pays twice to be told the same thing by a second model and
hides the real fault behind the second message; NOT falling back on a 503
leaves the whole feature doing nothing on exactly the days it was built for.
"""

import pytest

from openlocalweather.llm.errors import LLMResponseError, LLMUnavailableError
from openlocalweather.llm.fallback import FallbackProvider
from openlocalweather.llm.provider import provider_identity, served_identity
from openlocalweather.llm.schema import GeminiNarrativeResponse


class Recorder:
    """A provider that answers, or fails in a named way."""

    def __init__(self, model, answer=None, raises=None):
        self.model = model
        self.answer = answer
        self.raises = raises
        self.calls = 0
        self.before_attempt = None
        self.after_attempt = None
        self.after_response = None
        self.seen_identity = None
        self.chain = None

    def generate(self, system_prompt, user_prompt, response_schema):
        self.calls += 1
        # What the spend ledger would have written at the moment of the call.
        if self.chain is not None:
            self.seen_identity = provider_identity(self.chain)
        if self.raises is not None:
            raise self.raises
        return self.answer


class Polling(Recorder):
    """A provider with the Interactions API's extra hook."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.on_poll = None


def test_an_unavailable_vendor_falls_through_to_the_next():
    down = Recorder("gemini-3.6-flash", raises=LLMUnavailableError("HTTP 503"))
    up = Recorder("nvidia/nemotron", answer="written")
    assert FallbackProvider([down, up]).generate("s", "u", None) == "written"
    assert (down.calls, up.calls) == (1, 1)


def test_a_bad_answer_does_not_fall_through():
    """The expensive mistake. A response that fails schema validation is our
    prompt, our schema, or this model's inability to follow them, and the next
    model is handed exactly the same two."""
    bad = Recorder("gemini-3.6-flash", raises=LLMResponseError("failed schema validation"))
    spare = Recorder("nvidia/nemotron", answer="written")
    with pytest.raises(LLMResponseError, match="schema validation"):
        FallbackProvider([bad, spare]).generate("s", "u", None)
    assert spare.calls == 0, "a schema failure paid for a second opinion"


def test_the_last_failure_is_what_reaches_the_caller():
    """Its TYPE is what the caller branches on: a judgment call aborts the run
    and a narrative call degrades the issuance, so a chain that wrapped the
    failure in something new would change what happens above it."""
    first = Recorder("a", raises=LLMUnavailableError("HTTP 503"))
    second = Recorder("b", raises=LLMUnavailableError("HTTP 500"))
    with pytest.raises(LLMUnavailableError, match="HTTP 500"):
        FallbackProvider([first, second]).generate("s", "u", None)


def test_the_cap_reaches_every_provider_in_the_chain():
    """Named in `test_spend_coverage.CAPPED_BY_FORWARDING`, which allowlists
    `FallbackProvider.generate` on exactly this promise. An AST cannot see a
    property setter fan an assignment out to a list, so the guard points here
    and here has to be real."""
    first, second = Recorder("a"), Polling("b")
    chain = FallbackProvider([first, second])

    def before():
        return None

    def after(outcome, elapsed_s):
        return None

    def response(meta):
        return None

    def poll():
        return None

    chain.before_attempt = before
    chain.after_attempt = after
    chain.after_response = response
    chain.on_poll = poll

    for provider in (first, second):
        assert provider.before_attempt is before
        assert provider.after_attempt is after
        assert provider.after_response is response
    assert second.on_poll is poll
    # Only to the child that has one: giving the others the attribute would
    # make `hasattr` lie about them, and attach_spend_cap branches on it.
    assert not hasattr(first, "on_poll")


def test_the_ledger_is_told_which_vendor_actually_spent():
    """The chain would otherwise write `FallbackProvider` and "unknown" on
    every row, and the ledger is what item 132's question rests on."""
    down = Recorder("gemini-3.6-flash", raises=LLMUnavailableError("HTTP 503"))
    up = Recorder("nvidia/nemotron-3-super-120b-a12b:free", answer="written")
    chain = FallbackProvider([down, up])
    down.chain = up.chain = chain

    chain.generate("s", "u", None)

    assert down.seen_identity == ("Recorder", "gemini-3.6-flash")
    assert up.seen_identity == ("Recorder", "nvidia/nemotron-3-super-120b-a12b:free")


def test_between_calls_the_chain_names_its_first_choice():
    """Not the last vendor that happened to answer. A stale `active_provider`
    would attribute the next caller's request to whoever served the previous
    one."""
    down = Recorder("first", raises=LLMUnavailableError("HTTP 503"))
    up = Recorder("second", answer="written")
    chain = FallbackProvider([down, up])
    chain.generate("s", "u", None)

    assert chain.active_provider is None
    assert provider_identity(chain) == ("FallbackProvider", "first")


def test_a_chain_needs_a_provider():
    with pytest.raises(ValueError, match="at least one"):
        FallbackProvider([])


def test_a_real_provider_whose_retries_are_spent_reports_itself_unavailable(monkeypatch):
    """THE MUTATION THAT SURVIVED, on both sides.

    Every test above hands the chain a stub that raises
    `LLMUnavailableError`, so all of them pass while the code that actually
    PRODUCES one raises something else. Checked 2026-09-21 by changing the
    retry loop's exit back to `LLMResponseError`: the Dart suite stayed green
    at 203 tests and the Python suite lost only an unrelated config assertion.

    The stubs pin the chain's branching. This pins the thing the branch is
    meant to catch, driven through a real provider and a real retry loop
    against an endpoint that only ever answers 503.
    """
    import requests
    from openlocalweather.llm import openai_compat
    from openlocalweather.llm.openai_compat import OpenAICompatProvider

    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    sent = []

    class Busy:
        status_code = 503
        headers: dict = {}
        text = '{"error":{"message":"over capacity"}}'

        def json(self):
            return {"error": {"message": "over capacity"}}

    monkeypatch.setattr(
        requests, "post", lambda *a, **k: (sent.append(k.get("json")), Busy())[1]
    )

    down = OpenAICompatProvider(
        api_key="k",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        base_url="https://openrouter.ai/api/v1",
    )
    spare = Recorder("spare", answer="written")

    assert (
        FallbackProvider([down, spare]).generate("s", "u", GeminiNarrativeResponse)
        == "written"
    )
    assert len(sent) == openai_compat.MAX_ATTEMPTS
    assert spare.calls == 1, "a spent retry budget did not fall through"


def test_a_real_provider_given_a_bad_request_does_not_report_itself_unavailable(
    monkeypatch,
):
    """The other half: a 400 is the key, the model id or the schema being
    wrong, and every later entry is handed the same one."""
    import requests
    from openlocalweather.llm.openai_compat import OpenAICompatProvider

    class BadRequest:
        status_code = 400
        headers: dict = {}
        text = '{"error":{"message":"unknown model"}}'

        def json(self):
            return {"error": {"message": "unknown model"}}

    monkeypatch.setattr(requests, "post", lambda *a, **k: BadRequest())

    down = OpenAICompatProvider(
        api_key="k", model="bad/model", base_url="https://openrouter.ai/api/v1"
    )
    spare = Recorder("spare", answer="written")

    with pytest.raises(LLMResponseError, match="unknown model"):
        FallbackProvider([down, spare]).generate("s", "u", GeminiNarrativeResponse)
    assert spare.calls == 0, "a bad request bought a second opinion"


def test_the_chain_remembers_which_link_actually_served():
    """ROADMAP item 171. The published record names the model that answered.

    `provider_identity` resolves through `active_provider` and is right DURING
    a call. The log entry is written after, when `generate` has cleared it in
    its `finally`, so anything asking then resolves back to the wrapper and
    gets `.model` — the FIRST link, whoever actually served.

    That is how the 2026-09-22 15:01Z entry came to name `gemini-3.6-flash`
    for a forecast written end to end by a fallback after Gemini returned four
    503s. `meta.llm_model` is what `replay.py` partitions the accuracy record
    by, so a wrong name does not just misdescribe one run: it files its scored
    forecast under a model that did not make it.
    """
    down = Recorder("gemini-3.6-flash", raises=LLMUnavailableError("503"))
    served = Recorder("nex-agi/nex-n2.5-pro:free", answer="prose")
    chain = FallbackProvider([down, served])

    assert chain.generate("s", "u", GeminiNarrativeResponse) == "prose"

    # After the call, not during: this is the moment the log entry is written.
    assert served_identity(chain) == ("Recorder", "nex-agi/nex-n2.5-pro:free")


def test_a_chain_that_never_served_reports_its_first_link():
    """Before any call there is no served link, and the honest answer to
    "what will this deployment use" is still the entry tried first."""
    a = Recorder("gemini-3.6-flash", answer="x")
    b = Recorder("backup", answer="y")

    assert served_identity(FallbackProvider([a, b])) == ("Recorder", "gemini-3.6-flash")


def test_a_bare_provider_is_its_own_served_identity():
    """A deployment with no chain answers exactly as it did before."""
    assert served_identity(Recorder("gemini-3.6-flash", answer="x")) == (
        "Recorder",
        "gemini-3.6-flash",
    )
