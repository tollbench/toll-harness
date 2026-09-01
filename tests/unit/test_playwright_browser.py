import sys
from pathlib import Path
from types import ModuleType

from toll_harness.browser.playwright import PlaywrightBrowserProvider


def test_playwright_profile_is_owner_only_and_reused(tmp_path, monkeypatch):
    launches = []

    class FakePage:
        url = "about:blank"

        def route(self, pattern, handler):
            self.route_registration = (pattern, handler)

    class FakeContext:
        def __init__(self, page):
            self.pages = [page]
            self.closed = False

        def new_page(self):  # pragma: no cover - page already exists
            raise AssertionError("existing persistent page should be reused")

        def close(self):
            self.closed = True

    class FakeChromium:
        def launch_persistent_context(self, *, user_data_dir, headless):
            page = FakePage()
            context = FakeContext(page)
            launches.append(
                {
                    "user_data_dir": user_data_dir,
                    "headless": headless,
                    "context": context,
                }
            )
            return context

        def launch(self, **_kwargs):  # pragma: no cover - persistent path expected
            raise AssertionError("ephemeral browser must not launch")

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()
            self.stopped = False

        def stop(self):
            self.stopped = True

    playwrappers = []

    class FakeStarter:
        def start(self):
            playwright = FakePlaywright()
            playwrappers.append(playwright)
            return playwright

    parent = ModuleType("playwright")
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakeStarter()
    parent.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", parent)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    profile = tmp_path / "agent-id" / "browser-profile"
    first = PlaywrightBrowserProvider(profile_directory=profile)
    first.close()
    second = PlaywrightBrowserProvider(profile_directory=profile)
    second.close()

    assert [Path(item["user_data_dir"]) for item in launches] == [profile, profile]
    assert all(item["headless"] is True for item in launches)
    assert profile.stat().st_mode & 0o777 == 0o700
    assert all(item["context"].closed for item in launches)
    assert all(playwright.stopped for playwright in playwrappers)
