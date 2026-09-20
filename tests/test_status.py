"""Logique pure de statut et de transitions (aucun réseau)."""

from datetime import datetime, timezone

from radarlib.status import (
    Observation,
    ProductState,
    Status,
    apply_observation,
    confirm,
    decide_status,
)

NOW = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)


def obs(web=None, stores=None, error=None, price=64.99):
    return Observation(
        product_id=1, title="Coffret", url="https://x/y.html", price=price,
        web=web, stores=None if stores is None else {s: f"Magasin {s}" for s in stores},
        checked_at=NOW, error=error,
    )


def test_decide_status():
    assert decide_status(True, {}) is Status.DISPONIBLE_WEB
    assert decide_status(True, {1: "a"}) is Status.DISPONIBLE_WEB
    assert decide_status(False, {1: "a"}) is Status.DISPONIBLE_MAGASIN
    assert decide_status(False, {}) is Status.INDISPONIBLE
    assert decide_status(None, None) is Status.INCONNU
    assert decide_status(None, {}) is Status.INCONNU
    assert decide_status(False, None) is Status.INCONNU


def test_transition_indispo_vers_dispo_web_une_seule_notif():
    state = ProductState.initial(1)
    state, alerts = apply_observation(state, obs(web=False, stores=[]))
    assert alerts == []
    assert state.status is Status.INDISPONIBLE

    state, alerts = apply_observation(state, obs(web=True, stores=[]))
    assert len(alerts) == 1
    assert alerts[0].kind == "web"
    assert state.status is Status.DISPONIBLE_WEB

    state, alerts = apply_observation(state, obs(web=True, stores=[]))
    assert alerts == []


def test_nouveau_magasin_declenche_une_notif_puis_plus():
    state = ProductState.initial(1)
    state, _ = apply_observation(state, obs(web=False, stores=[]))
    state, alerts = apply_observation(state, obs(web=False, stores=[10, 11]))
    assert len(alerts) == 1
    assert alerts[0].kind == "store"
    assert set(alerts[0].new_stores) == {"Magasin 10", "Magasin 11"}
    assert state.status is Status.DISPONIBLE_MAGASIN

    state, alerts = apply_observation(state, obs(web=False, stores=[10, 11]))
    assert alerts == []

    state, alerts = apply_observation(state, obs(web=False, stores=[10, 11, 12]))
    assert len(alerts) == 1
    assert alerts[0].new_stores == ["Magasin 12"]


def test_premiere_observation_disponible_notifie():
    state, alerts = apply_observation(ProductState.initial(1), obs(web=True, stores=[]))
    assert len(alerts) == 1


def test_inconnu_ne_notifie_jamais_et_garde_le_dernier_statut():
    state, _ = apply_observation(ProductState.initial(1), obs(web=True, stores=[5]))
    state, alerts = apply_observation(state, obs(web=None, stores=None, error="502"))
    assert alerts == []
    assert state.status is Status.DISPONIBLE_WEB
    assert state.web_available is True
    assert state.store_ids == [5]
    assert state.last_error == "502"
    assert state.consecutive_errors == 1

    # retour à la normale : rien de nouveau, donc pas de re-notif
    state, alerts = apply_observation(state, obs(web=True, stores=[5]))
    assert alerts == []
    assert state.consecutive_errors == 0


def test_retour_en_rupture_puis_reapparition_renotifie():
    state, _ = apply_observation(ProductState.initial(1), obs(web=True, stores=[]))
    state, alerts = apply_observation(state, obs(web=False, stores=[]))
    assert alerts == []
    state, alerts = apply_observation(state, obs(web=True, stores=[]))
    assert len(alerts) == 1


def test_stock_magasin_inconnu_mais_web_connu():
    state, alerts = apply_observation(ProductState.initial(1), obs(web=True, stores=None))
    assert len(alerts) == 1 and alerts[0].kind == "web"
    assert state.status is Status.DISPONIBLE_WEB
    assert state.store_ids == []
    assert state.stores_known is False


def test_confirmation_exige_deux_observations_concordantes():
    first = obs(web=True, stores=[1, 2])
    second = obs(web=False, stores=[2, 3])
    merged = confirm(first, second)
    assert merged.web is False
    assert set(merged.stores) == {2}

    assert confirm(first, obs(web=None, stores=None, error="timeout")).web is None


def test_prix_conserve_dans_l_etat():
    state, _ = apply_observation(ProductState.initial(1), obs(web=False, stores=[], price=14.99))
    assert state.price == 14.99
    state, _ = apply_observation(state, obs(web=None, stores=None, error="x", price=None))
    assert state.price == 14.99
