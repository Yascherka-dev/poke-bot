"""Étape 1 : exploration d'une fiche produit avec Playwright.

Ouvre la fiche, attend la fin du trafic réseau, journalise toutes les requêtes
XHR/fetch (URL, méthode, statut, extrait de réponse), sauvegarde une capture
d'écran et le DOM rendu dans ./debug/, et extrait window.__change['6'].

Usage : python explore.py [URL]
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_URL = (
    "https://www.lagranderecre.fr/jeux-de-societe/cartes-a-collectionner/"
    "coffret-dresseur-d-elite-pokemon-30e-anniversaire.html"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
BLOCKED_TYPES = {"image", "font", "media"}
BLOCKED_HOSTS = ("googletagmanager", "google-analytics", "didomi", "target2sell", "facebook", "doubleclick")
DEBUG_DIR = Path("debug")


async def run(url: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, locale="fr-FR", viewport={"width": 1280, "height": 900})

        async def route_handler(route, request):  # noqa: ANN001
            if request.resource_type in BLOCKED_TYPES or any(h in request.url for h in BLOCKED_HOSTS):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_handler)
        page = await context.new_page()

        async def on_response(response):  # noqa: ANN001
            req = response.request
            if req.resource_type not in {"xhr", "fetch"}:
                return
            try:
                body = await response.text()
            except Exception as exc:  # noqa: BLE001
                body = f"<body unavailable: {exc}>"
            log.append({
                "url": response.url,
                "method": req.method,
                "status": response.status,
                "post_data": (req.post_data or "")[:400],
                "response_excerpt": body[:400],
            })

        page.on("response", on_response)
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(2_000)

        await page.screenshot(path=DEBUG_DIR / f"{stamp}-fiche.png", full_page=True)
        (DEBUG_DIR / f"{stamp}-dom.html").write_text(await page.content(), encoding="utf-8")
        product = await page.evaluate("() => window.__change && window.__change['6']")
        (DEBUG_DIR / f"{stamp}-change6.json").write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")
        (DEBUG_DIR / f"{stamp}-xhr.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

        cart_box = await page.evaluate(
            """() => {
                const q = s => [...document.querySelectorAll(s)].map(e => ({
                    text: e.innerText.trim().slice(0, 80), disabled: !!e.disabled, cls: e.className}));
                return {
                    stock_label: q('.stock-availability'),
                    price: q('.price-value'),
                    buttons: q('[data-rbs-catalog-product-cart-box-dual] button'),
                };
            }"""
        )
        await browser.close()

    print(f"URL : {url}")
    print(f"Requêtes XHR/fetch : {len(log)}")
    for entry in log:
        print(f"  {entry['method']} {entry['status']} {entry['url']}")
    stock = (product or {}).get("stock", {})
    print("stock.webStore :", json.dumps(stock.get("webStore"), ensure_ascii=False))
    print("stock.store    :", json.dumps(stock.get("store"), ensure_ascii=False))
    print("price          :", (product or {}).get("price", {}).get("valueWithTax"))
    print("cart box rendu :", json.dumps(cart_box, ensure_ascii=False))
    print(f"Artefacts dans {DEBUG_DIR}/ (préfixe {stamp})")


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL))
