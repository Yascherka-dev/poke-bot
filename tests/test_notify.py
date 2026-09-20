"""Le client ntfy envoie les bons en-têtes et ne lève jamais vers l'appelant."""

import json

import httpx

from radarlib.notify import Notifier
from radarlib.status import Alert


def make_notifier(captured: list, status_code: int = 200) -> Notifier:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code, json={"id": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Notifier(server="https://ntfy.example", topic="topic-secret", client=client)


def test_alerte_disponible_est_urgente_avec_lien():
    captured: list[httpx.Request] = []
    n = make_notifier(captured)
    alert = Alert(kind="web", product_id=1, title="Coffret Dresseur d'élite", url="https://x/y.html",
                  price_label="64,99 €", new_stores=[])
    assert n.notify_alert(alert) is True
    req = captured[0]
    assert req.url == "https://ntfy.example/"
    body = json.loads(req.content)
    assert body["topic"] == "topic-secret"
    assert body["priority"] == 5
    assert "rotating_light" in body["tags"]
    assert body["click"] == "https://x/y.html"
    assert "64,99 €" in body["message"]
    assert "web" in body["message"].lower()


def test_alerte_magasin_liste_les_magasins_prioritaires_en_tete():
    captured: list[httpx.Request] = []
    n = make_notifier(captured)
    n.priority_stores = ["villeneuve"]
    alert = Alert(kind="store", product_id=1, title="Coffret", url="https://x", price_label="64,99 €",
                  new_stores=["La Grande Récré PASSY", "La Grande Récré VILLENEUVE LA GARENNE"])
    n.notify_alert(alert)
    message = json.loads(captured[0].content)["message"]
    assert message.index("VILLENEUVE") < message.index("PASSY")


def test_nouveau_produit_basse_priorite():
    captured: list[httpx.Request] = []
    make_notifier(captured).notify_new_product("Nouveau coffret", "https://x/new.html", "19,99 €")
    body = json.loads(captured[0].content)
    assert body["priority"] <= 2
    assert body["click"] == "https://x/new.html"


def test_echec_reseau_ne_leve_pas():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    n = Notifier(server="https://ntfy.example", topic="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert n.notify_test() is False


def test_statut_http_non_2xx_renvoie_false():
    captured: list[httpx.Request] = []
    assert make_notifier(captured, status_code=429).notify_test() is False
