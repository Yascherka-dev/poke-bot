"""Le cycle du radar : observer, confirmer, notifier, persister."""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from radarlib.config import Config
from radarlib.fetch import STORE_API_ACTION, STORE_API_EXTRA, FetchError, FetcherProtocol, store_availability_payload
from radarlib.notify import Notifier
from radarlib.parse import ParseError, ProductInfo, parse_product_list, parse_product_page, parse_store_availability
from radarlib.state import RadarState
from radarlib.status import Alert, Observation, ProductState, Status, apply_observation, confirm, price_label

log = logging.getLogger("radar")


def now() -> datetime:
    return datetime.now(timezone.utc)


class Radar:
    def __init__(self, config: Config, fetcher: FetcherProtocol, notifier: Notifier, state: RadarState,
                 sleep: Callable[[float], None] = time.sleep, browser=None):
        self.config = config
        self.fetcher = fetcher
        self.notifier = notifier
        self.notifier.priority_stores = list(config.priority_stores)
        self.state = state
        self.sleep = sleep
        self.browser = browser  # repli Playwright, optionnel
        self.blocked = False
        self._block_level = 0
        self._parse_failures: dict[str, int] = {}
        self.version = 0  # incrémenté à chaque changement, pour l'UI
        self.refresh_event = threading.Event()
        self.stop_event = threading.Event()
        self._lock = threading.Lock()

    # ----- observation d'un produit -----

    def _fetch_product(self, url: str) -> ProductInfo:
        """HTML → ProductInfo, avec bascule sur le navigateur après deux parses ratés."""
        use_browser = self.browser is not None and (self.config.use_browser or self._parse_failures.get(url, 0) >= 2)
        if use_browser:
            return self.browser.get_product(url)
        html = self.fetcher.get_html(url)
        try:
            info = parse_product_page(html)
        except ParseError:
            self._parse_failures[url] = self._parse_failures.get(url, 0) + 1
            raise
        self._parse_failures[url] = 0
        return info

    def _fetch_stores(self, sku: str) -> dict[int, str]:
        """Union des magasins avec stock autour de chaque centre, filtrée sur stores.yaml."""
        found: dict[int, str] = {}
        for center in self.config.centers:
            payload = self.fetcher.post_api(STORE_API_ACTION, store_availability_payload(sku, center.lat, center.lng), **STORE_API_EXTRA)
            for hit in parse_store_availability(payload):
                if hit.has_stock and hit.store_id in self.config.store_ids:
                    found[hit.store_id] = self.config.store_names.get(hit.store_id, hit.name)
        return found

    def observe(self, url: str, product_id_hint: int | None = None) -> Observation:
        checked_at = now()
        errors: list[str] = []
        info: ProductInfo | None = None
        try:
            info = self._fetch_product(url)
        except (FetchError, ParseError) as exc:
            errors.append(f"fiche : {exc}")
            self._note_block(exc)
        stores: dict[int, str] | None = None
        if info is not None and info.sku:
            try:
                stores = self._fetch_stores(info.sku)
            except (FetchError, ParseError) as exc:
                errors.append(f"magasins : {exc}")
                self._note_block(exc)
        return Observation(
            product_id=info.product_id if info else (product_id_hint or 0),
            title=info.title if info else "",
            url=url,  # l'URL de suivi (config), pas la canonique : c'est la clé de l'état
            price=info.price if info else None,
            web=info.buyable if info else None,
            stores=stores,
            checked_at=checked_at,
            error="; ".join(errors) or None,
        )

    def _note_block(self, exc: Exception) -> None:
        if isinstance(exc, FetchError) and exc.is_block:
            self.blocked = True

    # ----- un cycle complet -----

    def run_cycle(self) -> None:
        started = time.monotonic()
        self.blocked = False
        urls = list(dict.fromkeys(self.config.product_urls + self.state.tracked_urls))
        all_failed = True
        pending: list[tuple[str, ProductState, Observation]] = []

        for url in urls:
            try:
                prev = self._state_for(url)
                obs = self.observe(url, prev.product_id if prev else None)
                if obs.error is None or obs.web is not None:
                    all_failed = False
                pending.append((url, prev or ProductState.initial(obs.product_id), obs))
            except Exception:  # noqa: BLE001 : un produit en erreur n'arrête pas les autres
                log.exception("erreur inattendue sur %s", url)

        # Deuxième contrôle uniquement pour ce qui vient de passer à disponible.
        to_confirm = [i for i, (_url, prev, obs) in enumerate(pending) if self._would_alert(prev, obs)]
        if to_confirm:
            log.info("%d disponibilité(s) à confirmer dans %.0f s", len(to_confirm), self.config.confirm_delay_seconds)
            self.sleep(self.config.confirm_delay_seconds)
            for i in to_confirm:
                url, prev, first = pending[i]
                second = self.observe(url, prev.product_id)
                pending[i] = (url, prev, confirm(first, second))

        for url, prev, obs in pending:
            new_state, alerts = apply_observation(prev, obs)
            key = str(new_state.product_id)
            with self._lock:
                self.state.products[key] = new_state
            self._log_product(new_state)
            for alert in alerts:
                self.notifier.notify_alert(alert)

        self._check_event_page()
        self._finish_cycle(all_failed, time.monotonic() - started)

    @staticmethod
    def _would_alert(prev: ProductState, obs: Observation) -> bool:
        return bool(apply_observation(prev, obs)[1])

    def _state_for(self, url: str) -> ProductState | None:
        for p in self.state.products.values():
            if p.url == url:
                return p
        return None

    def _log_product(self, p: ProductState) -> None:
        stores = f"{len(p.store_ids)} magasin(s)" if p.stores_known else "magasins inconnus"
        err = f" | erreur : {p.last_error}" if p.last_error else ""
        log.info("%-11s %s | %s | %s%s", p.status.value, price_label(p.price), p.title or p.url, stores, err)

    def _check_event_page(self) -> None:
        if not self.config.event_url:
            return
        try:
            products = parse_product_list(self.fetcher.get_html(self.config.event_url))
        except (FetchError, ParseError) as exc:
            log.warning("page événement illisible : %s", exc)
            self._note_block(exc)
            return
        known = set(self.state.known_event_ids)
        first_run = not known
        for p in products:
            if p.product_id in known:
                continue
            self.state.known_event_ids.append(p.product_id)
            if p.url and p.url not in self.config.product_urls and p.url not in self.state.tracked_urls:
                self.state.tracked_urls.append(p.url)
                log.info("nouveau produit sur la page événement : %s", p.title)
                if not first_run:
                    self.notifier.notify_new_product(p.title, p.url, price_label(p.price))

    def _finish_cycle(self, all_failed: bool, duration: float) -> None:
        st = self.state
        st.cycles += 1
        st.last_cycle_at = now().isoformat()
        st.last_cycle_duration = round(duration, 1)
        st.blocked = self.blocked
        if all_failed:
            st.consecutive_failed_cycles += 1
            if st.consecutive_failed_cycles >= self.config.failure_threshold and not st.down_notified:
                last_error = next((p.last_error for p in st.products.values() if p.last_error), None)
                self.notifier.notify_down(st.consecutive_failed_cycles, last_error)
                st.down_notified = True
        else:
            st.consecutive_failed_cycles = 0
            st.down_notified = False
        self._block_level = min(self._block_level + 1, 5) if self.blocked else 0
        st.last_message = "cycle terminé" + (" avec blocage (backoff)" if self.blocked else "")
        st.save()
        self.version += 1

    # ----- boucle -----

    def next_delay(self) -> float:
        base = self.config.interval_seconds
        if self._block_level:
            base = max(self.config.interval_on_block_seconds, min(60 * 2 ** self._block_level, 1800))
        jitter = random.uniform(-self.config.jitter_seconds, self.config.jitter_seconds) if self.config.jitter_seconds else 0
        return max(float(base + jitter), 120.0)

    def run_forever(self) -> None:
        self.state.started_at = self.state.started_at or now().isoformat()
        self.notifier.notify_started(len(self.config.product_urls), len(self.config.store_ids))
        while not self.stop_event.is_set():
            try:
                self.run_cycle()
            except Exception:  # noqa: BLE001
                log.exception("cycle en échec")
            delay = self.next_delay()
            self.state.next_cycle_at = datetime.fromtimestamp(time.time() + delay, tz=timezone.utc).isoformat()
            self.state.save()
            self.version += 1
            log.info("prochain cycle dans %.0f s", delay)
            self.refresh_event.clear()
            if self.refresh_event.wait(delay):
                log.info("rafraîchissement demandé depuis l'interface")
        self.fetcher.close()

    def request_refresh(self) -> None:
        self.refresh_event.set()

    def summary(self) -> str:
        lines = []
        for p in self.state.products.values():
            stores = ", ".join(p.store_names.get(i, str(i)) for i in p.store_ids) if p.store_ids else ("aucun" if p.stores_known else "inconnu")
            lines.append(f"{p.status.value:19} {price_label(p.price):>10}  {p.title}\n{'':32}magasins : {stores}")
        return "\n".join(lines)
