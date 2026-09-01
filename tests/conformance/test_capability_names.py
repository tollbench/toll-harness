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
        "browser.type_secret",
        "browser.wait",
        "secret.generate",
        "files.list",
        "files.read",
        "files.write",
        "human.request",
        "result.complete",
        "result.fail",
        # Added in 0.12.0: outward HTTP with SecretStore placeholders, and the
        # park-and-wake timer. Additions extend this set; existing names stay
        # frozen.
        "http.request",
        "wake.set_timer",
        "operator.observe",
        "operator.message",
    }
    assert all(
        definition.version == "1.0" for definition in build_standard_registry().definitions()
    )
