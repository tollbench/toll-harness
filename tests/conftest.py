import pytest


@pytest.fixture(autouse=True)
def _no_update_check_network(monkeypatch):
    """The suite never reaches PyPI or a bench: the update check is off by default.

    tests/unit/test_update_check.py turns it back on with fakes in place.
    """
    monkeypatch.setenv("TOLL_HARNESS_UPDATE_CHECK", "0")
