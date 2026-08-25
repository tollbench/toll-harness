from toll_harness.operator.capabilities import OPERATOR_CAPABILITIES
from toll_harness.tools.registry import build_standard_registry


def test_initial_capability_names_are_frozen():
    names = {definition.name for definition in build_standard_registry().definitions()}
    names.update(definition.name for definition in OPERATOR_CAPABILITIES)

    assert names == {
        "state.load",
        "state.save",
        "email.list",
        "email.read",
        "email.send",
        "email.reply",
        "web.search",
        "web.fetch",
        "browser.open",
        "browser.observe",
        "browser.click",
        "browser.type",
        "browser.wait",
        "files.list",
        "files.read",
        "files.write",
        "human.request",
        "result.complete",
        "result.fail",
        "operator.observe",
        "operator.message",
    }
    assert all(
        definition.version == "1.0" for definition in build_standard_registry().definitions()
    )
