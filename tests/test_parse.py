"""Le parseur reconnaît un produit dispo, un produit indispo, les listes et l'API magasin."""

import json
from pathlib import Path

import pytest

from radarlib.parse import (
    ParseError,
    iter_change_blocks,
    parse_product_list,
    parse_product_page,
    parse_store_availability,
)

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_fiche_indisponible_coffret_dresseur():
    info = parse_product_page(read("fiche_coffret_dresseur_indispo.html"))
    assert info.product_id == 95148290
    assert info.sku == "0196214144835"
    assert info.title == "Coffret Dresseur d'élite Pokémon 30e Anniversaire"
    assert info.price == 64.99
    assert info.price_label == "64,99 €"
    assert info.web_available is False
    assert info.web_threshold == "UNAVAILABLE"
    assert info.buyable is False


def test_fiche_disponible_academie():
    info = parse_product_page(read("fiche_academie_dispo.html"))
    assert info.sku == "896744"
    assert info.price_label == "29,99 €"
    assert info.web_available is True
    assert info.web_threshold == "AVAILABLE"
    assert info.buy_button_disabled is False
    assert info.buyable is True


def test_page_sans_bloc_produit_leve_parse_error():
    with pytest.raises(ParseError):
        parse_product_page("<html><body>Chargement en cours...</body></html>")


def test_page_evenement_liste_les_5_produits_anniversaire():
    products = parse_product_list(read("evenement_30_ans.html"))
    titles = sorted(p.title for p in products)
    assert len(products) == 5
    assert all("30e Anniversaire" in t for t in titles)
    assert all(p.web_available is False for p in products)
    assert {p.price for p in products} == {14.99, 29.99, 32.99, 64.99}


def test_page_categorie_sans_filtre_contient_des_produits_en_stock():
    products = parse_product_list(read("categorie_pokemon.html"), only_anniversaire=False)
    assert len(products) == 6
    assert sum(p.web_available for p in products) == 6


def test_page_categorie_avec_filtre_est_vide():
    assert parse_product_list(read("categorie_pokemon.html")) == []


def test_iter_change_blocks_ignore_les_blocs_non_objet():
    html = "window.__change['1'] = null; window.__change['2'] = {\"a\": {\"b\": \"}\"}};"
    blocks = list(iter_change_blocks(html))
    assert blocks == [("2", {"a": {"b": "}"}})]


def test_api_magasin_coffret_dresseur_11_magasins():
    hits = parse_store_availability(json.loads(read("api_storeshipping_p1_paris.json")))
    assert len(hits) == 11
    assert all(h.has_stock for h in hits)
    names = {h.name for h in hits}
    assert "La Grande Récré VILLENEUVE LA GARENNE" in names
    assert {h.zip_code for h in hits if "ITALIE" in h.name} == {"75013"}


def test_api_magasin_reponse_invalide():
    with pytest.raises(ParseError):
        parse_store_availability({"name": "x"})
