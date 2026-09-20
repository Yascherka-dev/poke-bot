"""state.json : persistance atomique du dernier état connu."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from radarlib.status import ProductState, Status

VERSION = 1


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _product_to_dict(p: ProductState) -> dict[str, Any]:
    return {
        "product_id": p.product_id, "title": p.title, "url": p.url, "price": p.price,
        "web_available": p.web_available, "store_ids": p.store_ids,
        "store_names": {str(k): v for k, v in p.store_names.items()}, "stores_known": p.stores_known,
        "status": p.status.value,
        "last_checked": p.last_checked.isoformat() if p.last_checked else None,
        "last_change": p.last_change.isoformat() if p.last_change else None,
        "last_error": p.last_error, "consecutive_errors": p.consecutive_errors,
    }


def _product_from_dict(d: dict[str, Any]) -> ProductState:
    return ProductState(
        product_id=int(d["product_id"]), title=d.get("title", ""), url=d.get("url", ""), price=d.get("price"),
        web_available=d.get("web_available"), store_ids=[int(x) for x in d.get("store_ids", [])],
        store_names={int(k): v for k, v in (d.get("store_names") or {}).items()},
        stores_known=bool(d.get("stores_known", False)),
        status=Status(d.get("status", "INCONNU")), last_checked=_dt(d.get("last_checked")),
        last_change=_dt(d.get("last_change")), last_error=d.get("last_error"),
        consecutive_errors=int(d.get("consecutive_errors", 0)),
    )


@dataclass
class RadarState:
    path: Path
    products: dict[str, ProductState] = field(default_factory=dict)  # clé : id produit en str (JSON)
    tracked_urls: list[str] = field(default_factory=list)  # produits découverts sur la page événement
    known_event_ids: list[int] = field(default_factory=list)
    started_at: str | None = None
    last_cycle_at: str | None = None
    last_cycle_duration: float | None = None
    cycles: int = 0
    consecutive_failed_cycles: int = 0
    down_notified: bool = False
    blocked: bool = False
    next_cycle_at: str | None = None
    last_message: str = ""

    @classmethod
    def load(cls, path: Path) -> RadarState:
        state = cls(path=path)
        if not path.exists():
            return state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = path.with_suffix(".corrupt.json")
            try:
                path.replace(backup)
            except OSError:
                pass
            return state
        for key, value in (raw.get("products") or {}).items():
            try:
                state.products[str(key)] = _product_from_dict(value)
            except (KeyError, ValueError, TypeError):
                continue
        radar = raw.get("radar") or {}
        state.tracked_urls = list(raw.get("tracked_urls") or [])
        state.known_event_ids = [int(x) for x in raw.get("known_event_ids") or []]
        state.started_at = radar.get("started_at")
        state.last_cycle_at = radar.get("last_cycle_at")
        state.last_cycle_duration = radar.get("last_cycle_duration")
        state.cycles = int(radar.get("cycles", 0))
        state.consecutive_failed_cycles = int(radar.get("consecutive_failed_cycles", 0))
        state.down_notified = bool(radar.get("down_notified", False))
        state.blocked = bool(radar.get("blocked", False))
        state.next_cycle_at = radar.get("next_cycle_at")
        state.last_message = radar.get("last_message", "")
        return state

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "products": {k: _product_to_dict(v) for k, v in self.products.items()},
            "tracked_urls": self.tracked_urls,
            "known_event_ids": self.known_event_ids,
            "radar": {
                "started_at": self.started_at, "last_cycle_at": self.last_cycle_at,
                "last_cycle_duration": self.last_cycle_duration, "cycles": self.cycles,
                "consecutive_failed_cycles": self.consecutive_failed_cycles, "down_notified": self.down_notified,
                "blocked": self.blocked, "next_cycle_at": self.next_cycle_at, "last_message": self.last_message,
            },
        }

    def save(self) -> None:
        """Écriture atomique : fichier temporaire puis rename, jamais de state.json à moitié écrit."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
