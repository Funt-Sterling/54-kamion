"""Regression tests for the vision adapter's v1 proposal boundary.

These mock the model call on purpose. They pin down how the backend behaves
when a model returns something wrong — which is exactly what you cannot
arrange reliably against a live API — and nothing here claims to measure
visual accuracy. The live-API proof is a separate manual run.

The load-bearing guarantees, all reproduced below:

* A vision model contributes *observations*. It cannot set a price and it
  cannot invent a field.
* A failed read is never an empty read: truncated, malformed and unschematic
  responses raise rather than degrade into a proposal that claims nothing.
* Perception is hint-blind. Nothing about what the uploader asked for can
  reach the prompt, because an answer shaped by the question is not evidence.
"""

import json

import pytest

from app.contract import ALL_VIEWS, PROMPT_VERSION, VISION_SCHEMA_VERSION
from app.services.proposal import ProposalSchemaError, parse_proposal
from app.services.vision import (
    PROPOSAL_JSON_SCHEMA,
    AnthropicVisionAdapter,
    MockVisionAdapter,
    VisionCallResult,
    VisionResponseError,
    build_prompt,
    get_vision_adapter,
)

FAKE_KEY = "sk-ant-not-a-real-key-0123456789"


# --- fixtures ---------------------------------------------------------------


def proposal_payload(**overrides) -> dict:
    """A schema-valid v1 envelope claiming nothing, for a test to break.

    Local to this module rather than shared: these tests must keep working
    against the raw contract even while the shared helpers migrate.
    """
    payload = {
        "schema_version": VISION_SCHEMA_VERSION,
        "subject": {"extent": "partial", "category": "unknown"},
        "views": {name: {"visibility": "absent", "usable": False} for name in ALL_VIEWS},
        "readings": [],
        "candidates": [],
        "findings": [],
        "image_limitations": [],
        "notes": "",
    }
    payload.update(overrides)
    return payload


def proposal_json(**overrides) -> str:
    return json.dumps(proposal_payload(**overrides))


class _FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeUsage:
    input_tokens = 1834
    output_tokens = 622


class _FakeMessage:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [_FakeBlock(text)]
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()


class _FakeMessages:
    """Records every request so a test can inspect what was actually sent."""

    def __init__(self, message: _FakeMessage, reject_structured: Exception | None = None):
        self._message = message
        self._reject_structured = reject_structured
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._reject_structured is not None and "output_config" in kwargs:
            raise self._reject_structured
        return self._message


class _FakeClient:
    def __init__(self, messages: _FakeMessages):
        self.messages = messages


@pytest.fixture
def make_adapter(monkeypatch, tmp_path):
    """An AnthropicVisionAdapter with a fake key and no network underneath."""

    def build(message: _FakeMessage, reject_structured: Exception | None = None):
        monkeypatch.setenv("VISION_ADAPTER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

        from app.config import get_settings

        get_settings.cache_clear()
        adapter = AnthropicVisionAdapter()
        get_settings.cache_clear()

        fake = _FakeMessages(message, reject_structured)
        adapter._client = _FakeClient(fake)
        return adapter, fake

    return build


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "walkaround.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 not a real jpeg, never decoded here")
    return path


# --- malformed / unusable envelopes -----------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("{not json at all", id="malformed"),
        pytest.param("   ", id="empty"),
        pytest.param("[1, 2, 3]", id="json_array"),
        pytest.param('"a string"', id="json_scalar"),
        pytest.param("I'm sorry, I can't analyze this image.", id="prose"),
    ],
)
def test_unusable_response_raises(raw):
    with pytest.raises(ProposalSchemaError):
        parse_proposal(raw)


def test_fenced_json_is_accepted():
    """Some responses still arrive wrapped in a code fence; that alone is
    not a reason to discard an otherwise valid envelope."""
    proposal = parse_proposal(f"```json\n{proposal_json()}\n```")
    assert proposal.schema_version == VISION_SCHEMA_VERSION


def test_truncated_json_is_rejected_not_half_parsed():
    """Observed against the live API: a photo with many visible components
    ran past max_tokens and the JSON was cut mid-string."""
    truncated = proposal_json()[: len(proposal_json()) // 2]
    with pytest.raises(ProposalSchemaError):
        parse_proposal(truncated)


def test_wrong_schema_version_is_rejected():
    """A stored run must be readable back against the schema it was made
    under, so a proposal that names a different version is not ours."""
    with pytest.raises(ProposalSchemaError):
        parse_proposal(proposal_json(schema_version="v0"))


# --- the model may never contribute a price ---------------------------------


def test_top_level_price_field_is_rejected_not_ignored():
    """The guarantee the whole design rests on. Note this REJECTS rather
    than filters: a whitelist can only drop the invented keys it already
    knows about, so the envelope is closed instead."""
    with pytest.raises(ProposalSchemaError) as exc:
        parse_proposal(proposal_json(estimated_price="2500000 TRY"))
    assert "estimated_price" in str(exc.value)


@pytest.mark.parametrize(
    "payload_kwargs",
    [
        pytest.param({"price": "2500000"}, id="price"),
        pytest.param({"asking_price": 2500000}, id="asking_price"),
        pytest.param({"estimated_value": "2.5M TRY"}, id="estimated_value"),
        pytest.param({"market_range": {"low": 1, "high": 2}}, id="market_range"),
    ],
)
def test_no_monetary_field_can_be_smuggled_in(payload_kwargs):
    with pytest.raises(ProposalSchemaError):
        parse_proposal(proposal_json(**payload_kwargs))


def test_price_cannot_hide_inside_a_nested_object():
    """Unknown-field rejection is not just top level — every object in the
    envelope is closed."""
    with pytest.raises(ProposalSchemaError):
        parse_proposal(
            proposal_json(
                findings=[
                    {
                        "component": "cab",
                        "observation": "clean paint",
                        "visibility": "visible",
                        "estimated_value": "2500000",
                    }
                ]
            )
        )


def test_json_schema_closes_every_object_against_invented_keys():
    """The same guarantee restated at the schema layer, so a structured
    call is constrained before the parser ever has to catch it."""
    assert PROPOSAL_JSON_SCHEMA["additionalProperties"] is False
    assert PROPOSAL_JSON_SCHEMA["properties"]["views"]["additionalProperties"] is False
    finding = PROPOSAL_JSON_SCHEMA["properties"]["findings"]["items"]
    assert finding["additionalProperties"] is False
    assert "visibility" in finding["required"]


# --- views: no visibility may be reached by accident ------------------------


def test_every_view_key_is_mandatory():
    """A missing key used to mean "absent", which made the most convenient
    answer also the cheapest one to produce."""
    for view in ALL_VIEWS:
        views = {name: {"visibility": "absent", "usable": False} for name in ALL_VIEWS}
        views.pop(view)
        with pytest.raises(ProposalSchemaError) as exc:
            parse_proposal(proposal_json(views=views))
        assert view in str(exc.value)


def test_unknown_view_name_is_rejected():
    views = {name: {"visibility": "absent", "usable": False} for name in ALL_VIEWS}
    views["undercarriage"] = {"visibility": "visible", "usable": True}
    with pytest.raises(ProposalSchemaError):
        parse_proposal(proposal_json(views=views))


def test_missing_visibility_is_a_schema_error_not_a_default():
    views = {name: {"visibility": "absent", "usable": False} for name in ALL_VIEWS}
    views["side"] = {"usable": False}
    with pytest.raises(ProposalSchemaError):
        parse_proposal(proposal_json(views=views))


def test_usable_requires_visible():
    """"I could not see it, but it was usable" is not a coherent claim, and
    admission credits coverage from exactly this pair."""
    views = {name: {"visibility": "absent", "usable": False} for name in ALL_VIEWS}
    views["odometer"] = {"visibility": "unclear", "usable": True}
    with pytest.raises(ProposalSchemaError) as exc:
        parse_proposal(proposal_json(views=views))
    assert "usable" in str(exc.value)


def test_finding_without_visibility_is_rejected():
    with pytest.raises(ProposalSchemaError):
        parse_proposal(
            proposal_json(findings=[{"component": "chassis", "observation": "no rust seen"}])
        )


# --- readings ---------------------------------------------------------------


def test_trip_meter_is_representable_as_not_total():
    """The prompt asks for this distinction; the schema has to be able to
    carry the answer, or the model has nowhere to be honest."""
    proposal = parse_proposal(
        proposal_json(
            readings=[
                {
                    "kind": "total_odometer",
                    "raw_text": "412.6",
                    "readability": "readable",
                    "unit": "km",
                    "is_total": False,
                }
            ]
        )
    )
    assert proposal.readings[0].is_total is False


def test_odometer_unit_outside_the_contract_is_rejected():
    with pytest.raises(ProposalSchemaError):
        parse_proposal(
            proposal_json(
                readings=[
                    {
                        "kind": "total_odometer",
                        "raw_text": "365000",
                        "readability": "readable",
                        "unit": "furlongs",
                    }
                ]
            )
        )


def test_raw_text_is_preserved_verbatim_not_normalized():
    """Separators are evidence about what the dial said. `365.000` is not
    the parser's to repair into `365000` — admission decides, and abstains
    when the digits are ambiguous."""
    proposal = parse_proposal(
        proposal_json(
            readings=[
                {
                    "kind": "total_odometer",
                    "raw_text": "365.000",
                    "readability": "readable",
                    "unit": "km",
                    "is_total": True,
                }
            ]
        )
    )
    assert proposal.readings[0].raw_text == "365.000"


# --- the prompt is hint-blind -----------------------------------------------


def test_analyze_image_takes_no_component_hint():
    """The audit's worst finding: the prompt told the model what the
    uploader said the photo was, so the model agreed. The parameter is gone
    from the interface, not merely unused."""
    import inspect

    for adapter in (MockVisionAdapter, AnthropicVisionAdapter):
        params = inspect.signature(adapter.analyze_image).parameters
        assert "component_hint" not in params
        assert list(params) == ["self", "image_path"]


def test_build_prompt_takes_no_arguments():
    import inspect

    assert list(inspect.signature(build_prompt).parameters) == []


@pytest.mark.parametrize(
    "phrase",
    ["uploader", "tagged", "hint", "requested", "asked for", "labelled", "labeled"],
)
def test_prompt_never_mentions_what_was_requested(phrase):
    assert phrase not in build_prompt().lower()


def test_prompt_states_the_constraints_admission_depends_on():
    prompt = build_prompt().lower()
    # Every view key is mandatory.
    for view in ALL_VIEWS:
        assert view in prompt
    assert "must be present" in prompt
    # Never guess a visibility.
    assert "never guess a visibility" in prompt
    # usable=true requires visibility=visible.
    assert '"usable": true requires "visibility": "visible"' in prompt
    # A trip meter is not a total.
    assert "trip meter" in prompt and '"is_total": false' in prompt
    # raw_text is literal.
    assert "raw_text is literal" in prompt
    # Never a price.
    assert "never output a price" in prompt


def test_prompt_is_actually_sent_to_the_model(make_adapter, photo):
    adapter, fake = make_adapter(_FakeMessage(proposal_json()))
    adapter.analyze_image(photo)

    content = fake.calls[0]["messages"][0]["content"]
    text_blocks = [block["text"] for block in content if block["type"] == "text"]
    assert text_blocks == [build_prompt()]
    assert any(block["type"] == "image" for block in content)


# --- the adapter call -------------------------------------------------------


def test_analyze_image_returns_a_parsed_proposal_and_run_metadata(make_adapter, photo):
    raw = proposal_json(subject={"extent": "whole", "category": "tractor_unit"})
    adapter, _ = make_adapter(_FakeMessage(raw))

    result = adapter.analyze_image(photo)

    assert isinstance(result, VisionCallResult)
    assert result.proposal.subject_category == "tractor_unit"
    assert result.raw_text == raw
    assert result.model_id == "claude-sonnet-5"
    assert result.prompt_version == PROMPT_VERSION
    assert result.schema_version == VISION_SCHEMA_VERSION
    assert result.usage == {"input_tokens": 1834, "output_tokens": 622}
    assert result.latency_ms >= 0
    assert result.stop_reason == "end_turn"


def test_call_result_never_carries_the_api_key(make_adapter, photo):
    """An InferenceRun is persisted and surfaced; a credential that reached
    it would reach a log line and an API response too."""
    adapter, _ = make_adapter(_FakeMessage(proposal_json()))

    result = adapter.analyze_image(photo)

    assert FAKE_KEY not in repr(result)
    assert FAKE_KEY not in json.dumps(result.usage)
    assert FAKE_KEY not in result.raw_text


def test_truncated_at_max_tokens_raises_before_parsing(make_adapter, photo):
    """The budget running out must not surface as "unterminated string at
    line 22", and half a response must never become half an analysis."""
    adapter, _ = make_adapter(_FakeMessage(proposal_json()[:200], stop_reason="max_tokens"))

    with pytest.raises(VisionResponseError) as exc:
        adapter.analyze_image(photo)
    assert "token limit" in str(exc.value)


def test_truncation_error_is_catchable_as_a_schema_error(make_adapter, photo):
    """Callers want one answer to "may anything here be admitted?"."""
    adapter, _ = make_adapter(_FakeMessage(proposal_json()[:200], stop_reason="max_tokens"))

    with pytest.raises(ProposalSchemaError):
        adapter.analyze_image(photo)


def test_unschematic_model_output_raises_rather_than_returning_empty(make_adapter, photo):
    adapter, _ = make_adapter(_FakeMessage('{"schema_version": "v1"}'))

    with pytest.raises(ProposalSchemaError):
        adapter.analyze_image(photo)


# --- structured output ------------------------------------------------------


def test_structured_output_is_requested_with_the_v1_schema(make_adapter, photo):
    adapter, fake = make_adapter(_FakeMessage(proposal_json()))

    result = adapter.analyze_image(photo)

    assert result.structured_output is True
    output_config = fake.calls[0]["output_config"]
    assert output_config["format"]["type"] == "json_schema"
    assert output_config["format"]["schema"] is PROPOSAL_JSON_SCHEMA


def _bad_request(message: str):
    import httpx

    import anthropic

    return anthropic.BadRequestError(
        message,
        response=httpx.Response(
            400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        body=None,
    )


def test_schema_rejection_falls_back_to_plain_text_json(make_adapter, photo):
    """Structured output is a convenience; the strict parser is the
    guarantee. If this account or model will not take the schema, the
    photo is still read rather than lost."""
    adapter, fake = make_adapter(
        _FakeMessage(proposal_json()),
        reject_structured=_bad_request("output_config.format: unsupported for this model"),
    )

    result = adapter.analyze_image(photo)

    assert result.structured_output is False
    assert result.proposal.schema_version == VISION_SCHEMA_VERSION
    assert len(fake.calls) == 2
    assert "output_config" not in fake.calls[1]


def test_schema_rejection_is_learned_once_not_retried_per_photo(make_adapter, photo):
    adapter, fake = make_adapter(
        _FakeMessage(proposal_json()),
        reject_structured=_bad_request("json_schema is not supported"),
    )

    adapter.analyze_image(photo)
    adapter.analyze_image(photo)

    # 2 for the first photo (rejected + fallback), 1 for the second.
    assert len(fake.calls) == 3
    assert "output_config" not in fake.calls[2]


def test_unrelated_bad_request_is_not_swallowed_by_the_fallback(make_adapter, photo):
    """A 400 about the image itself is a real failure. Retrying it would
    buy a second charge and the same error."""
    adapter, fake = make_adapter(
        _FakeMessage(proposal_json()),
        reject_structured=_bad_request("image exceeds 5 MB"),
    )

    with pytest.raises(Exception) as exc:
        adapter.analyze_image(photo)
    assert "image exceeds" in str(exc.value)
    assert len(fake.calls) == 1


# --- the mock fabricates nothing --------------------------------------------


def test_mock_adapter_returns_a_schema_valid_proposal_claiming_nothing(photo):
    result = MockVisionAdapter().analyze_image(photo)

    assert isinstance(result, VisionCallResult)
    # Round-trips through the real parser, so it cannot drift out of schema.
    assert parse_proposal(result.raw_text) == result.proposal

    proposal = result.proposal
    assert proposal.subject_category == "unknown"
    assert proposal.readings == ()
    assert proposal.candidates == ()
    assert proposal.findings == ()
    assert set(proposal.views) == set(ALL_VIEWS)
    assert all(view.visibility == "absent" for view in proposal.views.values())
    assert all(view.usable is False for view in proposal.views.values())


def test_mock_adapter_credits_no_coverage_and_no_claims(photo):
    """Stated against admission, because "claims nothing" is a fact about
    what survives the pipeline, not about the JSON's shape."""
    from app.services.admission import admit

    result = admit(MockVisionAdapter().analyze_image(photo).proposal)

    assert result.views == []
    assert result.claims == []
    assert result.findings == []


def test_mock_adapter_says_it_is_a_mock(photo):
    result = MockVisionAdapter().analyze_image(photo)
    assert result.model_id == "mock"
    assert "mock" in result.proposal.notes.lower()


# --- adapter selection ------------------------------------------------------


def test_get_vision_adapter_defaults_to_mock():
    assert isinstance(get_vision_adapter(), MockVisionAdapter)


def test_get_vision_adapter_selects_anthropic_when_configured(monkeypatch):
    monkeypatch.setenv("VISION_ADAPTER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert isinstance(get_vision_adapter(), AnthropicVisionAdapter)
    finally:
        get_settings.cache_clear()


def test_anthropic_adapter_refuses_to_run_without_a_key(monkeypatch):
    """Better a loud failure at construction than a silent fall back to the
    mock, which would look like a successful analysis that saw nothing."""
    monkeypatch.setenv("VISION_ADAPTER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            AnthropicVisionAdapter()
    finally:
        get_settings.cache_clear()
