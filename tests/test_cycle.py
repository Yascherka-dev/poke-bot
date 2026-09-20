"""Cycle complet avec un fetcher factice : une transition = exactement une notif, confirmée."""

import json
from pathlib import Path

import httpx

from radarlib.config import Config, Center
from radarlib.core import Radar
from radarlib.fetch import FetchError
from radarlib.notify import Notifier
from radarlib.state import RadarState

FIXTURES = Path(__file__).parent / "fixtures"
URL_INDISPO = "https://www.lagranderecre.fr/jeux-de-societe/cartes-a-collectionner/coffret-dresseur-d-elite-pokemon-30e-anniversaire.html"


class FakeFetcher:
    """Renvoie des fixtures, et permet de simuler un restock ou une panne."""

    def __init__(self):
        self.html = (FIXTURES / "fiche_coffret_dresseur_indispo.html").read_text(encoding="utf-8")
        self.api = {"items": []}
        self.fail_api = False
        self.fail_html = False
        self.calls = []

    def get_html(self, url):
        self.calls.append(("GET", url))
        if self.fail_html:
            raise FetchError("blocked", "HTTP 502", status=502)
        return self.html

    def post_api(self, action, data, **extra):
        self.calls.append(("POST", action))
        if self.fail_api:
            raise FetchError("blocked", "HTTP 502", status=502)
        return self.api

    def close(self):
        pass


def make_radar(tmp_path, fetcher, sent):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    notifier = Notifier(server="https://ntfy.example", topic="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = Config(
        product_urls=[URL_INDISPO], event_url=None, interval_seconds=180, jitter_seconds=0,
        api_min_gap_seconds=0, interval_on_block_seconds=300, confirm_delay_seconds=0,
        centers=[Center("Paris", 48.8566, 2.3522)], priority_stores=[], store_ids={84203041, 93402726},
        store_names={84203041: "Italie 2", 93402726: "Villeneuve"}, ntfy_server="https://ntfy.example", ntfy_topic="t",
        state_file=tmp_path / "state.json", log_file=tmp_path / "radar.log", failure_threshold=5,
        ui_host="127.0.0.1", ui_port=8765, use_browser=False,
    )
    state = RadarState.load(config.state_file)
    return Radar(config=config, fetcher=fetcher, notifier=notifier, state=state, sleep=lambda s: None)


def test_indispo_puis_dispo_web_une_notif_puis_aucune(tmp_path):
    fetcher, sent = FakeFetcher(), []
    radar = make_radar(tmp_path, fetcher, sent)

    radar.run_cycle()
    assert sent == []
    assert radar.state.products["95148290"].status == "INDISPONIBLE"

    fetcher.html = (FIXTURES / "fiche_academie_dispo.html").read_text(encoding="utf-8").replace('"id":74213184', '"id":95148290')
    radar.run_cycle()
    assert len(sent) == 1
    assert sent[0]["priority"] == 5
    assert radar.state.products["95148290"].status == "DISPONIBLE_WEB"

    radar.run_cycle()
    assert len(sent) == 1


def test_dispo_non_confirmee_ne_notifie_pas(tmp_path):
    fetcher, sent = FakeFetcher(), []
    radar = make_radar(tmp_path, fetcher, sent)
    radar.run_cycle()

    dispo = (FIXTURES / "fiche_academie_dispo.html").read_text(encoding="utf-8").replace('"id":74213184', '"id":95148290')
    indispo = fetcher.html
    sequence = iter([dispo, indispo])
    fetcher.get_html = lambda url: next(sequence)
    radar.run_cycle()
    assert sent == []
    assert radar.state.products["95148290"].status == "INDISPONIBLE"


def test_magasin_apparait_notifie_et_filtre_hors_regions(tmp_path):
    fetcher, sent = FakeFetcher(), []
    radar = make_radar(tmp_path, fetcher, sent)
    radar.run_cycle()

    fetcher.api = json.loads((FIXTURES / "api_storeshipping_p1_paris.json").read_text(encoding="utf-8"))
    radar.run_cycle()
    assert len(sent) == 1
    product = radar.state.products["95148290"]
    assert product.status == "DISPONIBLE_MAGASIN"
    assert set(product.store_ids) == {84203041, 93402726}  # Essômes, Jaux... hors liste : ignorés
    assert "Villeneuve" in sent[0]["message"]

    radar.run_cycle()
    assert len(sent) == 1


def test_api_en_502_donne_inconnu_et_pas_de_notif(tmp_path):
    fetcher, sent = FakeFetcher(), []
    radar = make_radar(tmp_path, fetcher, sent)
    fetcher.fail_api = True
    radar.run_cycle()
    product = radar.state.products["95148290"]
    assert product.status == "INDISPONIBLE"  # le web est connu
    assert product.stores_known is False
    assert "502" in (product.last_error or "")
    assert sent == []
    assert radar.blocked is True


def test_une_seule_entree_par_url_meme_si_l_id_change(tmp_path):
    """Un state.json hérité du mode mock (faux ids) ne doit pas doubler les produits."""
    fetcher, sent = FakeFetcher(), []
    radar = make_radar(tmp_path, fetcher, sent)
    fetcher.html = fetcher.html.replace('"id":95148290', '"id":90000001')
    radar.run_cycle()
    assert list(radar.state.products) == ["90000001"]

    fetcher.html = (FIXTURES / "fiche_coffret_dresseur_indispo.html").read_text(encoding="utf-8")
    radar.run_cycle()
    assert list(radar.state.products) == ["95148290"]
    assert sent == []


def test_page_en_panne_5_cycles_declenche_notif_radar_en_panne(tmp_path):
    fetcher, sent = FakeFetcher(), []
    radar = make_radar(tmp_path, fetcher, sent)
    fetcher.fail_html = True
    for _ in range(5):
        radar.run_cycle()
    down = [m for m in sent if "panne" in m["title"].lower()]
    assert len(down) == 1
    radar.run_cycle()
    assert len([m for m in sent if "panne" in m["title"].lower()]) == 1
