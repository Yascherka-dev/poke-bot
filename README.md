# Radar de stock Pokémon 30 ans, La Grande Récré

Surveille les fiches produit de la collection Pokémon 30e anniversaire sur lagranderecre.fr et envoie une notification push (ntfy) sur ton téléphone dès qu'un produit devient achetable sur le web ou apparaît en stock dans un des 34 magasins d'Île-de-France, de Normandie et de Bretagne. Une interface web locale montre tout en temps réel.

Ce que le radar ne fait pas, par choix : aucun achat, aucun ajout au panier, aucune connexion à un compte, aucun contournement de captcha ou de blocage, aucun proxy.

## Comment ça marche

- Le stock web est lu directement dans le HTML de chaque fiche (données rendues côté serveur), un GET par produit.
- Le stock magasin vient de l'API de la fenêtre « disponibilité en magasin » du site, interrogée par produit autour de cinq centres (Paris, Rouen, Caen, Rennes, Quimper), puis filtrée sur les 34 magasins de `stores.yaml`.
- Une disponibilité n'est notifiée qu'après un second contrôle 10 s plus tard, et une seule fois par transition. Une erreur donne « inconnu », jamais « disponible ».
- Le site est ménagé : intervalle de 180 s (plancher 120 s), jitter ±30 s, 3 s minimum entre deux appels, ralentissement automatique à 5 min sur 403/429/502 ou timeout.

Les détails de l'exploration (endpoints, champs, sélecteurs, incident 502) sont dans [NOTES.md](NOTES.md).

## Installation

```bash
git clone https://github.com/Yascherka-dev/poke-bot.git
cd poke-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium      # uniquement pour le repli navigateur, optionnel
cp .env.example .env             # puis renseigner NTFY_TOPIC
```

Choisis un topic ntfy long et imprévisible, par exemple `yas-pkmn-` suivi de 12 caractères aléatoires : sur ntfy.sh, quiconque connaît le nom du topic peut lire tes notifications.

```bash
python -c "import secrets; print('yas-pkmn-' + secrets.token_hex(6))"
```

## S'abonner au topic sur le téléphone

1. Installer l'application **ntfy** (App Store sur iOS, Play Store ou F-Droid sur Android).
2. Appuyer sur **+** / « Subscribe to topic », saisir exactement le nom du topic de ton `.env`, serveur `ntfy.sh` par défaut.
3. Sur iOS, autoriser les notifications quand l'app le demande. Sur Android, désactiver l'optimisation de batterie pour ntfy si les notifications arrivent en retard.
4. Vérifier :

```bash
python radar.py --test-notif
```

Le téléphone doit sonner avec « Test radar Pokémon ». Les alertes de stock sont envoyées en priorité urgente, avec le lien de la fiche : un tap ouvre la page.

## Lancer

```bash
python radar.py                 # boucle continue dans le terminal
python radar.py --serve         # boucle + interface sur http://127.0.0.1:8765
python radar.py --once          # un seul passage, code de sortie 0 (cron, CI)
python radar.py --mock --once   # rejoue les fixtures, aucun appel au site
python radar.py --fallback      # force le rendu Playwright pour les fiches
python radar.py --check-url URL # diagnostic : ce que le parseur lit sur une fiche
```

L'interface (`--serve`) affiche chaque produit avec son statut, le prix, la liste des magasins qui l'ont en stock (magasins prioritaires en vert), la dernière erreur éventuelle, l'état du radar et les 50 dernières lignes du journal. Le bouton « Rafraîchir maintenant » lance un cycle immédiat, avec un délai de 60 s entre deux clics pour respecter la cadence.

Pour laisser tourner en arrière-plan sur un Mac :

```bash
nohup python radar.py --serve > /dev/null 2>&1 &
```

## Configuration

`config.yaml` : URLs surveillées, page événement, intervalle, centres de recherche, magasins prioritaires, port de l'interface. Le topic ntfy vit dans `.env` (`NTFY_TOPIC`), jamais dans `config.yaml`.

`stores.yaml` : les 34 magasins, généré par `python tools/build_stores.py` depuis les pages région du site. À relancer si un magasin ouvre ou ferme.

`state.json` : dernier état connu, écrit à chaque cycle. Le supprimer remet le radar à zéro, ce qui renotifie ce qui est disponible au premier passage.

## Tests

```bash
pytest
```

Les tests tournent sur des fixtures enregistrées pendant l'exploration (fiches en stock et en rupture, page événement, réponses de l'API magasin) et ne font aucun appel réseau. Ils couvrent le parseur, la logique de transition (une transition = exactement une notification, puis plus rien), la confirmation à 10 s, le filtrage des magasins hors régions, le 502 qui donne « inconnu », et la notification « radar en panne » après 5 cycles en erreur.

## GitHub Actions, filet de sécurité

`.github/workflows/radar.yml` lance `python radar.py --once` toutes les 5 minutes, avec `state.json` conservé entre deux runs via le cache d'Actions.

1. Dans le dépôt GitHub : Settings → Secrets and variables → Actions → **New repository secret**, nom `NTFY_TOPIC`, valeur ton topic.
2. Onglet Actions → activer les workflows si GitHub le demande.

Deux limites à connaître. Le cron GitHub est souvent en retard de plusieurs minutes, donc pour un restock qui part vite, la boucle locale reste la méthode principale. Et les requêtes partent d'IP de datacenter Microsoft, que le site peut refuser : si le journal du workflow montre des 403 ou un captcha alors que tout marche en local, désactive le cron et laisse tourner le radar chez toi, sans chercher à contourner.

## Si le site bloque

Le radar ralentit tout seul. Si les erreurs persistent, monte `interval_seconds` à 300 dans `config.yaml`, arrête le radar quelques heures, puis relance. Ne pas ajouter de proxy.
