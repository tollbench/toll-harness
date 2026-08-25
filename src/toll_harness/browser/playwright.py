from __future__ import annotations

from typing import Any

from toll_harness.browser.base import BrowserProvider
from toll_harness.tools.web import _validate_public_url


class PlaywrightBrowserProvider(BrowserProvider):
    """Optional local browser using the same Toll browser schema exposed to every model."""

    def __init__(self, *, headless: bool = True, max_text_chars: int = 20_000):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError("Install Toll Harness with the 'browser' extra") from error
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._page = self._browser.new_page()
        self._protect_page()
        self._refs: dict[str, Any] = {}
        self.max_text_chars = max_text_chars

    def _protect_page(self) -> None:
        def handle_route(route, request):
            if request.url.startswith(("data:", "blob:", "about:")):
                route.continue_()
                return
            try:
                _validate_public_url(request.url)
            except (OSError, ValueError):
                route.abort()
                return
            route.continue_()

        self._page.route("**/*", handle_route)

    def open(self, url: str) -> dict[str, Any]:
        _validate_public_url(url)
        response = self._page.goto(url, wait_until="domcontentloaded")
        return {
            "url": self._page.url,
            "status": response.status if response else None,
            "title": self._page.title(),
        }

    def observe(self) -> dict[str, Any]:
        self._refs = {}
        elements = []
        locator = self._page.locator("a,button,input,textarea,select,[role=button]")
        for index in range(min(locator.count(), 200)):
            item = locator.nth(index)
            if not item.is_visible():
                continue
            ref = f"e{len(elements) + 1}"
            self._refs[ref] = item
            try:
                label = (
                    item.get_attribute("aria-label")
                    or item.inner_text()
                    or item.get_attribute("placeholder")
                )
            except Exception:
                label = ""
            elements.append(
                {
                    "ref": ref,
                    "tag": item.evaluate("element => element.tagName.toLowerCase()"),
                    "label": (label or "").strip()[:300],
                }
            )
        return {
            "url": self._page.url,
            "title": self._page.title(),
            "text": self._page.locator("body").inner_text()[: self.max_text_chars],
            "elements": elements,
        }

    def _element(self, ref: str):
        if ref not in self._refs:
            raise KeyError("Unknown element ref; call browser.observe again")
        return self._refs[ref]

    def click(self, ref: str) -> dict[str, Any]:
        self._element(ref).click()
        self._page.wait_for_load_state("domcontentloaded")
        return {"clicked": ref, "url": self._page.url}

    def type(self, ref: str, text: str, submit: bool = False) -> dict[str, Any]:
        element = self._element(ref)
        element.fill(text)
        if submit:
            element.press("Enter")
            self._page.wait_for_load_state("domcontentloaded")
        return {"typed": ref, "submitted": submit, "url": self._page.url}

    def wait(self, seconds: float) -> dict[str, Any]:
        bounded = min(max(seconds, 0), 30)
        self._page.wait_for_timeout(bounded * 1000)
        return {"waited_seconds": bounded, "url": self._page.url}

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()
