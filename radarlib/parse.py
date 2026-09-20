"""Extraction des données produit et magasin depuis le HTML et les réponses API.

Le site (plateforme Proximis) rend côté serveur des blocs
``window.__change['<id>'] = {...};`` dans des <script> inline. Le bloc de la
fiche produit contient prix et stock ; les pages liste contiennent
``productsData`` ; les pages région contiennent ``storesData``.

Tout ici est pur : aucune requête réseau, facilement testable sur fixtures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

_BLOCK_RE = re.compile(r"window\.__change\['(\d+)'\]\s*=\s*")
_ANNIVERSAIRE_RE = re.compile(r"30e?\s*anniversaire", re.IGNORECASE)


class ParseError(ValueError):
    """Le HTML ne contient pas la structure attendue."""


def _extract_json_object(text: str, start: int) -> str | None:
    """Renvoie l'objet JSON qui commence à ``start`` (accolade ouvrante), par équilibrage.

    Une regex ne peut pas délimiter un objet JSON imbriqué ; on compte les
    accolades en ignorant celles qui sont dans des chaînes.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def iter_change_blocks(html: str) -> Iterator[tuple[str, Any]]:
    """Itère sur les ``(id, objet)`` des blocs ``window.__change['id'] = {...}``."""
    for match in _BLOCK_RE.finditer(html):
        raw = _extract_json_object(html, match.end())
        if raw is None:
            continue
        try:
            yield match.group(1), json.loads(raw)
        except json.JSONDecodeError:
            continue


@dataclass(frozen=True)
class ProductInfo:
    """Ce qu'on retient d'une fiche produit."""

    product_id: int
    title: str
    url: str
    sku: str
    price: float | None
    web_available: bool
    web_threshold: str
    store_available: bool | None = None  # stock du magasin lié au cookie, si présent
    restock_date: str | None = None
    buy_button_disabled: bool = True  # cartBox.shippingCategories.disabled

    @property
    def buyable(self) -> bool:
        """Disponible web = stock déclaré ET bouton d'achat actif (deux signaux, jamais un seul)."""
        return self.web_available and not self.buy_button_disabled

    @property
    def price_label(self) -> str:
        if self.price is None:
            return "prix inconnu"
        return f"{self.price:.2f}".replace(".", ",") + " €"


def _product_from_block(data: dict[str, Any]) -> ProductInfo:
    common = data["common"]
    stock = data.get("stock") or {}
    web = stock.get("webStore") or {}
    store = stock.get("store")
    price = (data.get("price") or {}).get("valueWithTax")
    cart_box = data.get("cartBox") or {}
    shipping = cart_box.get("shippingCategories") or {}
    return ProductInfo(
        product_id=int(common["id"]),
        title=str(common.get("title", "")).strip(),
        url=str((common.get("URL") or {}).get("canonical", "")),
        sku=str(stock.get("sku") or ""),
        price=float(price) if price is not None else None,
        web_available=bool(web.get("available")),
        web_threshold=str(web.get("threshold") or "UNKNOWN"),
        store_available=bool(store.get("available")) if isinstance(store, dict) else None,
        restock_date=shipping.get("restockDate") or None,
        # Sans cartBox (pages liste), on se fie au seul stock déclaré.
        buy_button_disabled=bool(shipping.get("disabled", not web.get("available"))),
    )


def parse_product_page(html: str) -> ProductInfo:
    """Fiche produit → ProductInfo. Lève ParseError si le bloc produit manque."""
    for _key, block in iter_change_blocks(html):
        if isinstance(block, dict) and "stock" in block and isinstance(block.get("common"), dict) and "id" in block["common"]:
            return _product_from_block(block)
    raise ParseError("bloc produit window.__change absent (page incomplète, redirection ou blocage ?)")


def parse_product_from_change(data: dict[str, Any]) -> ProductInfo:
    """Variante pour le repli Playwright, qui lit ``window.__change['6']`` directement."""
    if not isinstance(data, dict) or "stock" not in data:
        raise ParseError("objet __change sans clé stock")
    return _product_from_block(data)


def parse_product_list(html: str, only_anniversaire: bool = True) -> list[ProductInfo]:
    """Page liste (événement, catégorie) → produits, dédupliqués par id.

    ``only_anniversaire`` ne garde que la collection « 30e anniversaire »,
    pour ignorer les blocs de recommandation qui accompagnent la page événement.
    """
    seen: dict[int, ProductInfo] = {}
    for _key, block in iter_change_blocks(html):
        if not isinstance(block, dict) or not isinstance(block.get("productsData"), list):
            continue
        for item in block["productsData"]:
            if not isinstance(item, dict) or not isinstance(item.get("common"), dict):
                continue
            try:
                info = _product_from_block(item)
            except (KeyError, TypeError, ValueError):
                continue
            if not info.title or not info.url:
                continue  # emplacements vides ou produits non publiés
            if only_anniversaire and not (_ANNIVERSAIRE_RE.search(info.title) or _ANNIVERSAIRE_RE.search(info.url)):
                continue
            seen.setdefault(info.product_id, info)
    return list(seen.values())


@dataclass(frozen=True)
class StoreHit:
    """Un magasin renvoyé par l'API de disponibilité (donc qui a le produit)."""

    store_id: int
    name: str
    zip_code: str
    city: str
    has_stock: bool


def parse_store_availability(payload: dict[str, Any]) -> list[StoreHit]:
    """Réponse de ``Rbs/Storeshipping/Store/`` → magasins avec stock.

    L'API ne renvoie que les magasins qui ont le produit, mais on garde le
    booléen ``storeShipping.hasStoreStock`` comme garde-fou explicite.
    """
    items = payload.get("items")
    if not isinstance(items, list):
        raise ParseError("réponse API sans liste items")
    hits: list[StoreHit] = []
    for it in items:
        try:
            common = it["common"]
            fields = (it.get("address") or {}).get("fields") or {}
            hits.append(StoreHit(
                store_id=int(common["id"]),
                name=str(common.get("title", "")).strip(),
                zip_code=str(fields.get("zipCode") or ""),
                city=str(fields.get("locality") or ""),
                has_stock=bool((it.get("storeShipping") or {}).get("hasStoreStock")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return hits
