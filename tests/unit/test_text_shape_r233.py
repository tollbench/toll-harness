"""RULE 233 -- what you hand back in words has a shape too.

WHAT FORCED IT (production deal 91221abe, 2026-09-05): step 3 promised a stop
card for each approved restaurant with address, hours, suggested order and one
dish, and the agent filed a document whose blocks were those four words as
headings with nothing under them. The person sent it back; the same shell came
again. The bench grew the blank and three refusals; the harness never filled the
blank, so every railed agent kept promising prose.
"""

from toll_harness.toll_bench import blocks
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry

from tests.unit.test_deliver_file_r230 import _StepApi

SHAPE = {"channel": "text", "fields": ["address", "hours"], "min_count": 2}

CARD_1 = {"address": "12 Main St", "hours": "9-5"}
CARD_2 = {"Address": "40 Elm Ave", "hours": "10-6"}  # a key's case is not a lie


def _document(*items, title="Stops"):
    return {"title": title, "blocks": [{"type": "cards", "items": list(items)}]}


def _outcome(document):
    return {"note": "Your stop cards.", "document": document}


# --------------------------------------------------------------------------
# 1. The blank on the plan: the mirror knows the shape.
# --------------------------------------------------------------------------


def test_a_text_step_that_names_nothing_is_prose_and_draws_nothing():
    assert blocks.deliverable_problems([{"title": "Findings", "deliverable": {"channel": "text"}}]) == []
    assert blocks.signed_fields({"channel": "text"}) == []
    assert blocks.signed_min_count({"channel": "text"}) == 1


def test_a_named_shape_passes_the_mirror():
    assert blocks.deliverable_problems([{"title": "Stops", "deliverable": SHAPE}]) == []
    assert blocks.signed_fields(SHAPE) == ["address", "hours"]
    assert blocks.signed_min_count(SHAPE) == 2
    assert blocks.shape_words(SHAPE) == "at least 2 cards with address and hours"


def test_field_names_are_read_the_way_the_bench_reads_them():
    fields, error = blocks.normalize_fields(["  Suggested   Order ", "dish", "", None])
    assert error is None
    assert fields == ["suggested order", "dish"]


def test_the_mirror_names_a_bad_field_in_the_doors_words():
    problems = blocks.deliverable_problems(
        [{"title": "Stops", "deliverable": {"channel": "text", "fields": ["address", "address"]}}]
    )
    assert [p["path"] for p in problems] == ["steps.0.deliverable.fields"]
    assert 'names "address" twice' in problems[0]["message"]

    problems = blocks.deliverable_problems(
        [{"title": "Stops", "deliverable": {"channel": "text", "fields": ["https://maps.example"]}}]
    )
    assert "is an address, not a field name" in problems[0]["message"]

    problems = blocks.deliverable_problems(
        [{"title": "Stops", "deliverable": {"channel": "text", "fields": [f"f{i}" for i in range(13)]}}]
    )
    assert "at most 12 names; you named 13" in problems[0]["message"]


def test_min_count_rides_with_fields_and_stays_a_whole_number():
    problems = blocks.deliverable_problems(
        [{"title": "Stops", "deliverable": {"channel": "text", "min_count": 3}}]
    )
    assert problems[0]["path"] == "steps.0.deliverable.min_count"
    assert "rides with deliverable.fields" in problems[0]["message"]

    problems = blocks.deliverable_problems(
        [{"title": "Stops", "deliverable": {"channel": "text", "fields": ["address"], "min_count": 0}}]
    )
    assert "from 1 to 200" in problems[0]["message"]

    problems = blocks.deliverable_problems(
        [{"title": "Stops", "deliverable": {"channel": "text", "fields": ["address"], "min_count": "two"}}]
    )
    assert '"two" is not one' in problems[0]["message"]


def test_a_copied_field_blank_is_named_and_then_stripped_not_filed():
    step = {
        "title": "Stops",
        "outcome_promise": "A card per stop",
        "deliverable": {"channel": "text", "fields": ["<field>"], "min_count": 2},
    }
    problems = blocks.deliverable_problems([step])
    assert "form's blank, not a field name" in problems[0]["message"]

    trimmed, cleared = blocks.clear_blank_deliverables({"steps": [step]})
    assert cleared == [0]
    # Nothing real was left, so the shape went with it: prose, and no word of ours.
    assert trimmed["steps"][0]["deliverable"] == {"channel": "text"}

    half = dict(step, deliverable={"channel": "text", "fields": ["address", "<field>"]})
    trimmed, cleared = blocks.clear_blank_deliverables({"steps": [half]})
    assert cleared == [0]
    assert trimmed["steps"][0]["deliverable"] == {"channel": "text", "fields": ["address"]}


def test_a_file_step_carries_no_shape():
    assert blocks.signed_fields({"channel": "file", "family": "video", "types": ["mp4"], "fields": ["x"]}) == []


# --------------------------------------------------------------------------
# 2. The door, run over the document in hand.
# --------------------------------------------------------------------------


def test_headings_with_nothing_under_them_hand_back_nothing():
    # Rodney's exact filing.
    document = {
        "blocks": [
            {"type": "heading", "text": "Address"},
            {"type": "heading", "text": "Hours"},
        ]
    }
    refusal = blocks.cards_shortfall(SHAPE, _outcome(document))
    assert refusal["error"] == "deliverable_fields_missing"
    assert refusal["message"] == (
        "This step promised 2 or more cards with address and hours; the document has no cards block."
    )
    assert refusal["how"]["document"]["blocks"][0]["type"] == "cards"
    assert refusal["how"]["document"]["blocks"][0]["items"] == [
        {"address": "<the address>", "hours": "<the hours>"}
    ]
    assert refusal["certain"] is False


def test_an_empty_box_is_refused_by_card_and_field():
    refusal = blocks.cards_shortfall(SHAPE, _outcome(_document(CARD_1, {"address": "40 Elm Ave", "hours": " "})))
    assert refusal["error"] == "deliverable_fields_blank"
    assert refusal["message"] == "Card 2 leaves hours empty. Every card has to fill address and hours."
    assert refusal["blank"] == {"card": 2, "fields": ["hours"]}
    assert refusal["certain"] is True

    refusal = blocks.cards_shortfall(SHAPE, _outcome(_document({"note": "a heading only"})))
    assert refusal["message"] == "Card 1 leaves address and hours empty. Every card has to fill address and hours."


def test_too_few_filled_cards_is_named_with_the_count():
    refusal = blocks.cards_shortfall(SHAPE, _outcome(_document(CARD_1)))
    assert refusal["error"] == "deliverable_count_short"
    assert refusal["message"] == "This step promised at least 2 cards; the document has 1."
    assert refusal["found"] == 1


def test_enough_filled_cards_pass_and_the_card_number_runs_across_blocks():
    document = {
        "blocks": [
            {"type": "paragraph", "text": "Two stops."},
            {"type": "cards", "items": [CARD_1]},
            {"type": "cards", "items": [CARD_2]},
        ]
    }
    assert blocks.cards_shortfall(SHAPE, _outcome(document)) is None
    assert [n for n, _ in blocks.cards_items(document)] == [1, 2]


def test_a_text_outcome_on_a_shaped_step_has_no_cards():
    refusal = blocks.cards_shortfall(SHAPE, {"note": "Done.", "text": "Address: 12 Main St. Hours: 9-5."})
    assert refusal["error"] == "deliverable_fields_missing"


def test_a_step_with_no_shape_counts_nothing():
    assert blocks.cards_shortfall({"channel": "text"}, _outcome({"blocks": [{"type": "heading", "text": "x"}]})) is None
    assert blocks.cards_shortfall(None, _outcome(_document())) is None


# --------------------------------------------------------------------------
# 3. The provider: said before the filing is spent, and never buried.
# --------------------------------------------------------------------------


def test_a_blank_box_is_refused_every_time_before_the_wire():
    api = _StepApi(SHAPE)
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")
    outcome = _outcome(_document(CARD_1, {"address": "40 Elm Ave", "hours": ""}))

    first = provider.file_outcome("target-1", outcome, "idem-1")
    second = provider.file_outcome("target-1", outcome, "idem-2")

    for refused in (first, second):
        assert refused["ok"] is False
        assert refused["error"] == "deliverable_fields_blank"
        assert refused["message"].startswith("Card 2 leaves hours empty.")
        assert "counts empty boxes" in refused["message"]
        assert refused["how"]["document"]["blocks"][0]["type"] == "cards"
        assert refused["terminal"] is False
    assert api.filed == []


def test_no_cards_is_named_once_and_then_the_door_decides():
    """An earlier receipt on the same step may already carry the cards, and
    `current-step` does not publish receipts, so the mirror speaks once."""
    api = _StepApi(SHAPE)
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")
    outcome = _outcome({"blocks": [{"type": "heading", "text": "Address"}]})

    first = provider.file_outcome("target-1", outcome, "idem-1")
    second = provider.file_outcome("target-1", outcome, "idem-2")

    assert first["error"] == "deliverable_fields_missing"
    assert second == {"ok": True, "receipt_id": "r-1"}
    assert len(api.filed) == 1


def test_filled_cards_go_to_the_bench():
    api = _StepApi(SHAPE)
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")

    result = provider.file_outcome("target-1", _outcome(_document(CARD_1, CARD_2)), "idem-1")

    assert result == {"ok": True, "receipt_id": "r-1"}
    _target, filed, _key = api.filed[0]
    assert filed["document"]["blocks"][0]["items"] == [CARD_1, CARD_2]


def test_a_prose_step_is_untouched():
    api = _StepApi({"channel": "text"})
    provider = BookOfHousesTollBenchProvider(api)
    provider.current_step("deal-1")

    result = provider.file_outcome(
        "target-1", _outcome({"blocks": [{"type": "heading", "text": "Address"}]}), "idem-1"
    )

    assert result == {"ok": True, "receipt_id": "r-1"}


def test_the_servers_shape_refusals_reach_the_model_verbatim():
    from toll_harness.email.book_of_houses import BookOfHousesApiError

    api = _StepApi(
        None,
        error=BookOfHousesApiError(
            422,
            "deliverable_count_short",
            "This step promised at least 7 cards; the document has 3.",
            body={"error": "deliverable_count_short", "how": {"method": "POST"}},
        ),
    )
    provider = BookOfHousesTollBenchProvider(api)

    result = provider.file_outcome("target-1", _outcome(_document(CARD_1)), "idem-1")

    assert result["ok"] is False
    assert result["error"] == "deliverable_count_short"
    assert result["message"] == "This step promised at least 7 cards; the document has 3."
    assert result["detail"]["how"] == {"method": "POST"}


# --------------------------------------------------------------------------
# 4. Where a raw model reads it.
# --------------------------------------------------------------------------


def test_the_cards_block_and_the_rule_are_described_where_a_model_reads_them():
    registry = add_toll_bench_tools(build_standard_registry())
    definitions = {item.name: item for item in registry.definitions()}
    tool = definitions["toll_bench.file_outcome"]

    assert "deliverable.fields" in tool.description
    assert "deliverable_fields_blank" in tool.description
    block = tool.input_schema["properties"]["outcome"]["properties"]["document"]["properties"]["blocks"]["items"]
    assert "cards" in block["properties"]["type"]["description"]
    assert {"type": "object", "additionalProperties": {"type": "string"}} in (
        block["properties"]["items"]["items"]["anyOf"]
    )


def test_every_planning_surface_names_the_shape():
    import inspect

    from toll_harness import cli
    from toll_harness.core import runtime

    runtime_words = inspect.getsource(runtime)
    cli_words = inspect.getsource(cli)
    assert runtime_words.count("WORDS HAVE A SHAPE TOO") == 1
    assert "min_count" in runtime_words and "cards" in runtime_words
    # The bidding goal and the informed-plan instruction both carry it.
    assert cli_words.count("WORDS HAVE A SHAPE TOO") == 2
    assert cli_words.count("min_count") >= 2
