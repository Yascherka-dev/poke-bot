"""Accès réseau : GET des fiches, POST de l'API Proximis, cadence et backoff.

Un seul ``httpx.Client`` réutilisé, un espacement minimal entre appels API,
et une classification des erreurs pour que la boucle décide du backoff.
``FixtureFetcher`` rejoue les fixtures sans réseau (tests et ``--mock``).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

log = logging.getLogger("radar.fetch")

BASE_URL = "https://www.lagranderecre.fr"
API_PATH = "/ajax.V1.php/fr_FR/"
# Contexte observé dans window.__change.navigationContext d'une fiche produit.
CONTEXT = {"websiteId": 100052, "sectionId": 103089, "pageId": 100312}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
BLOCK_STATUSES = {403, 429, 502, 503}


class FetchError(Exception):
    """kind : 'blocked' (403/429/502/503), 'timeout', 'http', 'network'."""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status

    @property
    def is_block(self) -> bool:
        return self.kind in ("blocked", "timeout")


class FetcherProtocol(Protocol):
    def get_html(self, url: str) -> str: ...
    def post_api(self, action: str, data: dict[str, Any], **extra: Any) -> dict[str, Any]: ...
    def close(self) -> None: ...


class Fetcher:
    def __init__(self, min_gap_seconds: float = 3.0, timeout: float = 30.0, client: httpx.Client | None = None):
        self.min_gap = min_gap_seconds
        self._last_call = 0.0
        self.client = client or httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            },
            timeout=timeout,
            follow_redirects=True,
            http2=False,
        )

    def _pace(self) -> None:
        wait = self.min_gap - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._pace()
        try:
            resp = self.client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise FetchError("timeout", f"timeout sur {url}") from exc
        except httpx.HTTPError as exc:
            raise FetchError("network", f"erreur réseau sur {url} : {exc}") from exc
        if resp.status_code in BLOCK_STATUSES:
            raise FetchError("blocked", f"HTTP {resp.status_code} sur {url}", status=resp.status_code)
        if resp.status_code >= 400:
            raise FetchError("http", f"HTTP {resp.status_code} sur {url}", status=resp.status_code)
        return resp

    def get_html(self, url: str) -> str:
        resp = self._send("GET", url)
        log.debug("GET %s → %s (%d o)", url, resp.status_code, len(resp.content))
        return resp.text

    def post_api(self, action: str, data: dict[str, Any], **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {**CONTEXT, "data": data, "referer": BASE_URL + "/", **extra}
        resp = self._send(
            "POST", BASE_URL + API_PATH + action, json=payload,
            headers={"X-HTTP-Method-Override": "GET", "Accept": "application/json", "Referer": BASE_URL + "/"},
        )
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise FetchError("http", f"réponse non JSON sur {action}") from exc
        if not isinstance(body, dict):
            raise FetchError("http", f"réponse inattendue sur {action}")
        log.debug("POST %s → %s", action, resp.status_code)
        return body

    def close(self) -> None:
        self.client.close()


def store_availability_payload(sku: str, lat: float, lng: float) -> dict[str, Any]:
    """Corps observé dans la directive rbsStoreshippingProductLocator (useAsDefault forcé à false)."""
    return {
        "search": {"address": None, "country": None, "coordinates": {"latitude": lat, "longitude": lng},
                   "processId": 0, "storeId": None, "useAsDefault": False},
        "skuQuantities": {sku: 1},
        "forReservation": True, "forPickUp": False, "allowSelect": False,
    }


STORE_API_ACTION = "Rbs/Storeshipping/Store/"
STORE_API_EXTRA = {"URLFormats": "canonical", "dataSetNames": "address,coordinates,hoursSummary"}


class FixtureFetcher:
    """Rejoue les fixtures de tests/fixtures : aucun appel réseau.

    Un fichier ``mock_scenario.json`` optionnel (dans le dossier de fixtures)
    permet de faire varier la réponse : {"html": {url: fixture}, "api": fixture}.
    """

    def __init__(self, fixtures_dir: Path):
        self.dir = fixtures_dir
        self.calls: list[tuple[str, str]] = []
        scenario_path = fixtures_dir / "mock_scenario.json"
        self.scenario = json.loads(scenario_path.read_text(encoding="utf-8")) if scenario_path.exists() else {}

    def get_html(self, url: str) -> str:
        self.calls.append(("GET", url))
        name = (self.scenario.get("html") or {}).get(url)
        if name is None:
            if "evenements" in url:
                name = "evenement_30_ans.html"
            elif "academie" in url:
                name = "fiche_academie_dispo.html"
            else:
                name = "fiche_coffret_dresseur_indispo.html"
        html = (self.dir / name).read_text(encoding="utf-8")
        if name.startswith("fiche_"):
            # Une seule fixture sert plusieurs URLs : on lui donne un id et un titre
            # dérivés de l'URL pour que chaque produit garde sa propre carte.
            slug = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
            fake_id = 90_000_000 + sum(ord(c) * (i + 1) for i, c in enumerate(slug)) % 9_000_000
            html = html.replace('"id":95148290', f'"id":{fake_id}').replace('"id":74213184', f'"id":{fake_id}')
            html = html.replace("Coffret Dresseur d'\\u00e9lite Pok\\u00e9mon 30e Anniversaire", "[mock] " + slug)
            html = html.replace("Acad\\u00e9mie de Combat Pok\\u00e9mon - Nouvelle \\u00e9dition", "[mock] " + slug)
        return html

    def post_api(self, action: str, data: dict[str, Any], **extra: Any) -> dict[str, Any]:
        self.calls.append(("POST", action))
        name = self.scenario.get("api") or "api_storeshipping_p1_paris.json"
        if name == "502":
            raise FetchError("blocked", "HTTP 502 (simulé)", status=502)
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def close(self) -> None:
        pass
