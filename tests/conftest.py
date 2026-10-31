import pytest


@pytest.fixture(autouse=True)
def _no_update_check_network(monkeypatch):
    """The suite never reaches PyPI or a bench: the update check is off by default.

    tests/unit/test_update_check.py turns it back on with fakes in place.
    """
    monkeypatch.setenv("TOLL_HARNESS_UPDATE_CHECK", "0")


@pytest.fixture(autouse=True)
def _no_service_manager_in_update_check(monkeypatch):
    """The update check never asks a real systemctl or launchctl about the service.

    tests/unit/test_install_service.py puts a fake state back where it needs one.
    """
    from toll_harness import updates

    monkeypatch.setattr(updates, "_service_check", lambda path, config: None)


@pytest.fixture(autouse=True)
def _package_logger_restored():
    """`cli.main()` configures the `toll_harness` logger once per process
    (a handler, a level, propagate off). A test that runs main() would leave
    every later test's caplog blind to this package's lines, so the logger is
    put back the way it was after each test."""
    import logging

    logger = logging.getLogger("toll_harness")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]
