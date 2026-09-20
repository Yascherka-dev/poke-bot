"""Logique pure : statut d'un produit et transitions qui méritent une notification.

Aucune E/S ici. Tout est testable sur des valeurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    DISPONIBLE_WEB = "DISPONIBLE_WEB"
    DISPONIBLE_MAGASIN = "DISPONIBLE_MAGASIN"
    INDISPONIBLE = "INDISPONIBLE"
    INCONNU = "INCONNU"


@dataclass(frozen=True)
class Observation:
    """Résultat d'un contrôle. ``None`` signifie « pas pu savoir »."""

    product_id: int
    title: str
    url: str
    price: float | None
    web: bool | None
    stores: dict[int, str] | None  # id → nom, magasins qui ont le produit
    checked_at: datetime
    error: str | None = None


@dataclass(frozen=True)
class Alert:
    kind: str  # "web" ou "store"
    product_id: int
    title: str
    url: str
    price_label: str
    new_stores: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductState:
    """Dernier état connu d'un produit, persisté dans state.json."""

    product_id: int
    title: str = ""
    url: str = ""
    price: float | None = None
    web_available: bool | None = None
    store_ids: list[int] = field(default_factory=list)
    store_names: dict[int, str] = field(default_factory=dict)
    stores_known: bool = False
    status: Status = Status.INCONNU
    last_checked: datetime | None = None
    last_change: datetime | None = None
    last_error: str | None = None
    consecutive_errors: int = 0

    @staticmethod
    def initial(product_id: int) -> ProductState:
        return ProductState(product_id=product_id)


def decide_status(web: bool | None, stores: dict[int, str] | None) -> Status:
    if web is True:
        return Status.DISPONIBLE_WEB
    if stores:
        return Status.DISPONIBLE_MAGASIN
    if web is False and stores is not None:
        return Status.INDISPONIBLE
    return Status.INCONNU


def price_label(price: float | None) -> str:
    if price is None:
        return "prix inconnu"
    return f"{price:.2f}".replace(".", ",") + " €"


def confirm(first: Observation, second: Observation) -> Observation:
    """Fusion de deux contrôles espacés : n'est disponible que ce qui l'est dans les deux.

    Un contrôle inconnu rend la fusion inconnue sur cette dimension, donc pas d'alerte.
    """
    web = True if (first.web is True and second.web is True) else (None if None in (first.web, second.web) else False)
    if first.stores is None or second.stores is None:
        stores = None
    else:
        stores = {k: v for k, v in first.stores.items() if k in second.stores}
    return replace(second, web=web, stores=stores, error=second.error or first.error)


def apply_observation(prev: ProductState, obs: Observation) -> tuple[ProductState, list[Alert]]:
    """Calcule le nouvel état et les alertes à émettre.

    Règles :
    - une dimension inconnue (``None``) ne modifie pas la dernière valeur connue et n'alerte jamais ;
    - alerte « web » quand le web passe à disponible alors qu'il ne l'était pas (ou jamais vu) ;
    - alerte « store » quand au moins un magasin apparaît qui n'était pas dans la liste connue.
    """
    alerts: list[Alert] = []
    label = price_label(obs.price if obs.price is not None else prev.price)

    web_available = prev.web_available if obs.web is None else obs.web
    if obs.web is True and prev.web_available is not True:
        alerts.append(Alert("web", obs.product_id, obs.title or prev.title, obs.url or prev.url, label))

    if obs.stores is None:
        store_ids, store_names, stores_known = prev.store_ids, prev.store_names, False
    else:
        store_ids = sorted(obs.stores)
        store_names = dict(obs.stores)
        stores_known = True
        new_ids = [sid for sid in store_ids if sid not in prev.store_ids]
        if new_ids:
            alerts.append(Alert("store", obs.product_id, obs.title or prev.title, obs.url or prev.url, label,
                                new_stores=[obs.stores[sid] for sid in new_ids]))

    if obs.web is None and obs.stores is None:
        status = prev.status  # contrôle entièrement raté : on garde tout tel quel
    else:
        known_stores = {sid: store_names.get(sid, "") for sid in store_ids}
        # Le web connu suffit à trancher INDISPONIBLE même si le stock magasin est inconnu ;
        # l'interface signale à part que le stock magasin n'a pas pu être lu (stores_known).
        status = decide_status(web_available, known_stores if (stores_known or web_available is not None) else None)

    changed = status != prev.status or store_ids != prev.store_ids or web_available != prev.web_available
    new_state = ProductState(
        product_id=obs.product_id,
        title=obs.title or prev.title,
        url=obs.url or prev.url,
        price=obs.price if obs.price is not None else prev.price,
        web_available=web_available,
        store_ids=store_ids,
        store_names=store_names,
        stores_known=stores_known,
        status=status,
        last_checked=obs.checked_at,
        last_change=obs.checked_at if changed else prev.last_change,
        last_error=obs.error,
        consecutive_errors=prev.consecutive_errors + 1 if obs.error else 0,
    )
    return new_state, alerts
