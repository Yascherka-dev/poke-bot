"""Repli Playwright : un seul Chromium réutilisé, images et trackers bloqués.

Lit ``window.__change['6']`` après rendu, applique le même parseur que le
mode HTTP, et vérifie le libellé rendu comme troisième signal.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from radarlib.fetch import USER_AGENT, FetchError
from radarlib.parse import ParseError, ProductInfo, parse_product_from_change

log = logging.getLogger("radar.browser")

BLOCKED_TYPES = {"image", "font", "media"}
BLOCKED_HOSTS = ("googletagmanager", "google-analytics", "didomi", "target2sell", "facebook", "doubleclick", "abtasty")


class BrowserFetcher:
    def __init__(self, debug_dir: Path | None = None, timeout_ms: int = 60_000):
        from playwright.sync_api import sync_playwright  # import tardif : optionnel en CI

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=USER_AGENT, locale="fr-FR", viewport={"width": 1280, "height": 900})
        self._context.route("**/*", self._route)
        self._page = self._context.new_page()
        self.timeout_ms = timeout_ms
        self.debug_dir = debug_dir

    @staticmethod
    def _route(route, request) -> None:  # noqa: ANN001
        if request.resource_type in BLOCKED_TYPES or any(h in request.url for h in BLOCKED_HOSTS):
            route.abort()
        else:
            route.continue_()

    def get_product(self, url: str) -> ProductInfo:
        from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

        try:
            resp = self._page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
        except PlaywrightTimeout as exc:
            raise FetchError("timeout", f"navigateur : timeout sur {url}") from exc
        except PlaywrightError as exc:
            raise FetchError("network", f"navigateur : {exc}") from exc
        if resp is not None and resp.status in (403, 429, 502, 503):
            raise FetchError("blocked", f"navigateur : HTTP {resp.status} sur {url}", status=resp.status)
        data = self._page.evaluate("() => window.__change && window.__change['6']")
        if self.debug_dir:
            self.save_debug(url)
        info = parse_product_from_change(data)
        label = self._page.evaluate("() => (document.querySelector('.stock-availability') || {}).innerText || ''")
        if info.buyable and "INDISPONIBLE" in label.upper():
            raise ParseError("données et libellé rendu en désaccord sur la disponibilité")
        return info

    def save_debug(self, url: str) -> None:
        assert self.debug_dir is not None
        self.debug_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = url.rstrip("/").split("/")[-1].replace(".html", "")[:60]
        self._page.screenshot(path=self.debug_dir / f"{stamp}-{slug}.png", full_page=True)
        (self.debug_dir / f"{stamp}-{slug}.html").write_text(self._page.content(), encoding="utf-8")
        data = self._page.evaluate("() => window.__change && window.__change['6']")
        (self.debug_dir / f"{stamp}-{slug}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self) -> None:
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
