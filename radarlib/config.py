"""Chargement et validation de config.yaml, stores.yaml et .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

MIN_INTERVAL_SECONDS = 120  # plancher non contournable
DEFAULT_INTERVAL_SECONDS = 180


class ConfigError(ValueError):
    """Configuration invalide, message destiné à l'utilisateur."""


@dataclass(frozen=True)
class Center:
    name: str
    lat: float
    lng: float


@dataclass(frozen=True)
class Config:
    product_urls: list[str]
    event_url: str | None
    interval_seconds: int
    jitter_seconds: int
    api_min_gap_seconds: float
    interval_on_block_seconds: int
    confirm_delay_seconds: float
    centers: list[Center]
    priority_stores: list[str]
    store_ids: set[int]
    store_names: dict[int, str]
    ntfy_server: str
    ntfy_topic: str | None
    state_file: Path
    log_file: Path
    failure_threshold: int
    ui_host: str
    ui_port: int
    use_browser: bool
    stores: list[dict] = field(default_factory=list)


def _require_list_of_str(raw: object, key: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ConfigError(f"config.yaml : « {key} » doit être une liste de chaînes non vides")
    return [x.strip() for x in raw]


def load_stores(path: Path) -> list[dict]:
    if not path.exists():
        raise ConfigError(f"{path} introuvable : lancer python tools/build_stores.py")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stores = data.get("stores")
    if not isinstance(stores, list) or not stores:
        raise ConfigError(f"{path} : liste « stores » vide ou absente")
    for s in stores:
        if not isinstance(s, dict) or "id" not in s or "name" not in s:
            raise ConfigError(f"{path} : chaque magasin doit avoir id et name")
    return stores


def load_config(config_path: Path = Path("config.yaml"), stores_path: Path = Path("stores.yaml"),
                env_path: Path = Path(".env"), use_browser: bool = False) -> Config:
    load_dotenv(env_path)
    if not config_path.exists():
        raise ConfigError(f"{config_path} introuvable")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml : le document racine doit être un mapping")

    product_urls = _require_list_of_str(raw.get("products", []), "products")
    if not product_urls:
        raise ConfigError("config.yaml : au moins une URL dans « products »")
    for url in product_urls:
        if not url.startswith("https://www.lagranderecre.fr/"):
            raise ConfigError(f"URL hors périmètre : {url}")

    interval = int(raw.get("interval_seconds", DEFAULT_INTERVAL_SECONDS))
    if interval < MIN_INTERVAL_SECONDS:
        raise ConfigError(f"interval_seconds={interval} : le plancher est {MIN_INTERVAL_SECONDS} s, non contournable")

    centers_raw = raw.get("centers") or []
    centers = []
    for c in centers_raw:
        try:
            centers.append(Center(str(c["name"]), float(c["lat"]), float(c["lng"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"config.yaml : centre invalide {c!r}") from exc
    if not centers:
        raise ConfigError("config.yaml : au moins un centre de recherche dans « centers »")

    stores = load_stores(stores_path)
    regions = raw.get("regions")
    if regions:
        wanted = set(_require_list_of_str(regions, "regions"))
        stores = [s for s in stores if s.get("region") in wanted]
        if not stores:
            raise ConfigError(f"aucun magasin dans les régions {sorted(wanted)}")

    ntfy_raw = raw.get("ntfy") or {}
    topic = os.environ.get("NTFY_TOPIC") or ntfy_raw.get("topic") or None
    server = (os.environ.get("NTFY_SERVER") or ntfy_raw.get("server") or "https://ntfy.sh").rstrip("/")

    ui_raw = raw.get("ui") or {}
    return Config(
        product_urls=product_urls,
        event_url=raw.get("event_url") or None,
        interval_seconds=interval,
        jitter_seconds=int(raw.get("jitter_seconds", 30)),
        api_min_gap_seconds=float(raw.get("api_min_gap_seconds", 3)),
        interval_on_block_seconds=max(int(raw.get("interval_on_block_seconds", 300)), interval),
        confirm_delay_seconds=float(raw.get("confirm_delay_seconds", 10)),
        centers=centers,
        priority_stores=[s.lower() for s in _require_list_of_str(raw.get("priority_stores", []), "priority_stores")],
        store_ids={int(s["id"]) for s in stores},
        store_names={int(s["id"]): str(s["name"]) for s in stores},
        ntfy_server=server,
        ntfy_topic=topic,
        state_file=Path(raw.get("state_file", "state.json")),
        log_file=Path(raw.get("log_file", "radar.log")),
        failure_threshold=int(raw.get("failure_threshold", 5)),
        ui_host=str(ui_raw.get("host", "127.0.0.1")),
        ui_port=int(ui_raw.get("port", 8765)),
        use_browser=use_browser,
        stores=stores,
    )
