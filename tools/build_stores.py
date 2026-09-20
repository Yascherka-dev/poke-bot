"""Génère stores.yaml depuis les pages région du site (rendu serveur).

Les pages /magasins/region/<slug>/ portent window.__change['<id>'].storesData.
On télécharge chaque page (ou on lit reference/region_<slug>.html si présent),
on extrait les magasins et on écrit stores.yaml, trié par région puis code postal.

Usage : python tools/build_stores.py [--offline]
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radarlib.parse import iter_change_blocks  # noqa: E402

REGIONS = {"ile-de-france": "Île-de-France", "normandie": "Normandie", "bretagne": "Bretagne"}
BASE = "https://www.lagranderecre.fr/magasins/region/{slug}/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def load_html(slug: str, offline: bool) -> str:
    cached = Path("reference") / f"region_{slug}.html"
    if offline or cached.exists():
        return cached.read_text(encoding="utf-8", errors="replace")
    resp = httpx.get(BASE.format(slug=slug), headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    cached.parent.mkdir(exist_ok=True)
    cached.write_text(resp.text, encoding="utf-8")
    return resp.text


def extract_stores(html: str, region: str) -> list[dict]:
    for _key, block in iter_change_blocks(html):
        if isinstance(block, dict) and block.get("storesData"):
            stores = block["storesData"]
            break
    else:
        raise RuntimeError(f"storesData introuvable pour {region}")
    out = []
    for s in stores:
        fields = s["address"]["fields"]
        coords = s.get("coordinates") or {}
        out.append({
            "id": s["common"]["id"],
            "code": s["common"]["code"],
            "name": s["common"]["title"].strip(),
            "region": region,
            "zip": fields.get("zipCode"),
            "city": fields.get("locality"),
            "lat": coords.get("latitude"),
            "lng": coords.get("longitude"),
            "reservation": bool(s.get("allow", {}).get("allowReservation")),
            "pickup": bool(s.get("allow", {}).get("allowPickUp")),
        })
    return sorted(out, key=lambda x: (x["zip"] or "", x["name"]))


def main() -> None:
    offline = "--offline" in sys.argv
    stores = [st for slug, region in REGIONS.items() for st in extract_stores(load_html(slug, offline), region)]
    header = (
        "# Magasins La Grande Récré surveillés, générés par tools/build_stores.py\n"
        "# depuis les pages /magasins/region/<slug>/ du site. Ne pas éditer à la main.\n"
    )
    Path("stores.yaml").write_text(header + yaml.safe_dump({"stores": stores}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    by_region: dict[str, int] = {}
    for st in stores:
        by_region[st["region"]] = by_region.get(st["region"], 0) + 1
    print(f"stores.yaml écrit : {len(stores)} magasins", by_region)


if __name__ == "__main__":
    main()
