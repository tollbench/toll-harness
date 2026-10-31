"""RULE 233 -- what you hand back in words has a shape too.

WHAT FORCED IT (production deal 91221abe, 2026-09-05): step 3 promised a stop
card for each approved restaurant with address, hours, suggested order and one
dish, and the agent filed a document whose blocks were those four words as
headings with nothing under them. The person sent it back; the same shell came
again. The bench grew the blank and three refusals; the harness never filled the
blank, so every railed agent kept promising prose.
"""

from tests.unit.test_deliver_file_r230 import _StepApi
from toll_harness.toll_bench.book_of_houses import BookOfHousesTollBenchProvider
from toll_harness.tools.registry import add_toll_bench_tools, build_standard_registry

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


# --------------------------------------------------------------------------
# 2. The door, run over the document in hand.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 3. The provider: said before the filing is spent, and never buried.
# --------------------------------------------------------------------------


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
    outcome = tool.input_schema["properties"]["outcome"]
    block = outcome["properties"]["document"]["properties"]["blocks"]["items"]
    assert "cards" in block["properties"]["type"]["description"]
    assert {"type": "object", "additionalProperties": {"type": "string"}} in (
        block["properties"]["items"]["items"]["anyOf"]
    )


