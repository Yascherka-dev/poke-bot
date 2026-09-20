"""Notifications push via ntfy (publication JSON, UTF-8 sans souci d'en-têtes)."""

from __future__ import annotations

import logging

import httpx

from radarlib.status import Alert

log = logging.getLogger("radar.notify")

PRIORITY_URGENT = 5
PRIORITY_DEFAULT = 3
PRIORITY_LOW = 2


class Notifier:
    def __init__(self, server: str, topic: str | None, client: httpx.Client | None = None, timeout: float = 15.0,
                 dry_run: bool = False):
        self.server = server.rstrip("/")
        self.topic = topic
        self.client = client or httpx.Client(timeout=timeout)
        self.priority_stores: list[str] = []
        self.dry_run = dry_run  # mode mock : on journalise, on n'envoie rien

    @property
    def configured(self) -> bool:
        return bool(self.topic)

    def _publish(self, title: str, message: str, priority: int, tags: list[str], click: str | None = None) -> bool:
        if not self.topic:
            log.warning("NTFY_TOPIC absent : notification non envoyée (%s)", title)
            return False
        payload = {"topic": self.topic, "title": title, "message": message, "priority": priority, "tags": tags}
        if click:
            payload["click"] = click
        if self.dry_run:
            log.info("[DRY RUN] notification non envoyée : %s | %s", title, message.replace("\n", " / "))
            return True
        try:
            resp = self.client.post(self.server + "/", json=payload)
        except httpx.HTTPError as exc:
            log.error("ntfy injoignable : %s", exc)
            return False
        if resp.status_code >= 300:
            log.error("ntfy a répondu %s : %s", resp.status_code, resp.text[:200])
            return False
        log.info("notification envoyée : %s", title)
        return True

    def _sort_stores(self, names: list[str]) -> list[str]:
        def rank(name: str) -> tuple[int, str]:
            low = name.lower()
            for i, key in enumerate(self.priority_stores):
                if key in low:
                    return (i, low)
            return (len(self.priority_stores), low)
        return sorted(names, key=rank)

    def notify_alert(self, alert: Alert) -> bool:
        if alert.kind == "web":
            title = f"🟢 Dispo web : {alert.title}"
            message = f"{alert.price_label}, achetable sur le web (livraison). Fonce."
        else:
            stores = self._sort_stores(alert.new_stores)
            title = f"🏬 Dispo en magasin : {alert.title}"
            message = f"{alert.price_label}, en stock dans {len(stores)} magasin(s) :\n" + "\n".join(f"• {s}" for s in stores)
        return self._publish(title, message, PRIORITY_URGENT, ["rotating_light"], click=alert.url)

    def notify_new_product(self, title: str, url: str, price_label: str) -> bool:
        return self._publish("Nouveau produit 30 ans", f"{title}, {price_label}. Ajouté à la surveillance.",
                             PRIORITY_LOW, ["new"], click=url)

    def notify_started(self, n_products: int, n_stores: int) -> bool:
        return self._publish("Radar démarré", f"{n_products} produits, {n_stores} magasins surveillés.",
                             PRIORITY_LOW, ["satellite"])

    def notify_down(self, cycles: int, last_error: str | None) -> bool:
        return self._publish("Radar en panne", f"{cycles} cycles consécutifs en erreur. Dernière erreur : {last_error or 'inconnue'}",
                             PRIORITY_DEFAULT, ["warning"])

    def notify_test(self) -> bool:
        return self._publish("Test radar Pokémon", "Si tu lis ça, les notifications marchent. 🎉",
                             PRIORITY_URGENT, ["rotating_light", "white_check_mark"])
