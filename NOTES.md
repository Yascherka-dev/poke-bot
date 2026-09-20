# NOTES : exploration de lagranderecre.fr (étape 1)

Date : 2026-09-20, entre 22h00 et 22h35. Tout ce qui suit a été observé (curl, Playwright, navigateur, lecture du JavaScript du site). Rien n'est deviné. Artefacts : `debug/20260920-223017-*` (capture d'écran, DOM rendu, `window.__change['6']`, journal XHR), fixtures dans `tests/fixtures/`.

## 1. Conclusion principale : pas besoin de navigateur pour le stock web

La prémisse « le HTML brut contient seulement Chargement en cours » est fausse pour ce qui nous intéresse. Le site (plateforme Proximis, AngularJS) rend le **visuel** côté client, mais il injecte les **données** côté serveur dans des `<script>` inline de la forme `window.__change['6'] = {...};`. Un `curl` sans cookie ni JavaScript reçoit 650 Ko de HTML qui contiennent déjà le prix et le stock.

Le journal XHR de Playwright le confirme : la fiche produit ne fait que 4 appels réseau après chargement (`abtasty ua-parser`, `Rbs/Commerce/Cart`, deux `Rbs/Review/...`). **Aucun appel ne renvoie le prix ou le stock** : ils sont dans le HTML initial.

Conséquence : la méthode principale est un simple GET `httpx` par fiche, et Playwright n'est qu'un repli.

### Champs du bloc produit `window.__change['6']`

| Champ | Valeur observée, coffret Dresseur d'élite | Rôle |
|---|---|---|
| `common.id` | `95148290` | identifiant produit |
| `common.title`, `common.URL.canonical` | titre, URL | identité |
| `stock.sku` | `"0196214144835"` (= EAN) | clé pour l'API magasin |
| `stock.skuId` | `95148242` | id interne |
| `stock.webStore.available` | `false` | **stock web** (bool) |
| `stock.webStore.threshold` | `"UNAVAILABLE"` | `AVAILABLE` quand en stock |
| `stock.webStore.thresholdTitle` | `"En rupture"` | `"En stock"` quand dispo |
| `stock.store.available` | absent, ou bool si un magasin est sélectionné par cookie | stock du magasin courant |
| `price.valueWithTax` | `64.99` | prix TTC |
| `cartBox.shippingCategories.disabled` / `.hasStock` | `true` / `false` | état du bouton « En livraison » |
| `cartBox.shippingCategories.restockDate` | `null` | date de réassort annoncée, si le site en donne une |
| `cartBox.storeCategories.allowed` | `false` sur tous les produits vus | réservation en ligne désactivée |
| `jsonLd.offers.availability` | `https://schema.org/OutOfStock` | redondant avec `webStore` |

L'id de bloc (`'6'`) est celui du layout de la page produit. Le parseur (`radarlib/parse.py`) ne s'y fie pas : il itère tous les blocs `window.__change['<n>']` et prend celui qui a `stock` et `common.id`, par équilibrage d'accolades et non par regex.

### Preuve positive (critère d'acceptation n°4)

Fiche en stock testée : **Académie de Combat Pokémon, nouvelle édition** (`/jeux-de-societe/cartes-a-collectionner/academie-de-combat-pokemon-nouvelle-edition.html`, sku `896744`, 29,99 €).

- Données : `webStore.available: true`, `threshold: "AVAILABLE"`, `thresholdTitle: "En stock"`, `shippingCategories.disabled: false`.
- Rendu : bouton « EN LIVRAISON » **actif**, libellé `STOCK WEB : DISPONIBLE`, pas de bouton « Recevoir une alerte ».
- Résultat du parseur sur la fixture `tests/fixtures/fiche_academie_dispo.html` : `web: True AVAILABLE, 29,99 €`.

Sur les 5 produits cibles : `webStore.available: false`, bouton « EN LIVRAISON » avec `disabled`, libellé `STOCK WEB : INDISPONIBLE`, bouton « RECEVOIR UNE ALERTE » présent.

### Sélecteurs CSS du rendu (utiles seulement au repli Playwright)

- Bloc d'achat : `[data-rbs-catalog-product-cart-box-dual]`
- Bouton d'achat : `button[data-ng-click="addShippingCategoriesProduct()"]`, texte « En livraison », attribut `disabled` quand indisponible. Le libellé « Ajouter au panier » n'existe que dans les données (`cartBox.allCategories.button`), il n'est pas rendu sur ces fiches.
- Bouton d'alerte : `.availability-notification button[data-ng-click="openDialog()"]`, texte « Recevoir une alerte », présent uniquement si indisponible.
- Libellé de stock : `.stock-availability` (« STOCK WEB : INDISPONIBLE » ou « STOCK WEB : DISPONIBLE »), le span porte la classe `.unavailable` quand indisponible.
- Prix : `.price-value.scalapay-price` (« 64,99 € »). Attention, un premier `.price-value` contient le prix HT.
- Bouton de réservation magasin : **jamais rendu** sur ces produits, puisque `cartBox.storeCategories.allowed` vaut `false`. La « réservation sous 1h » n'est pas proposée sur cette collection ; le magasin sert seulement à l'information de stock.

## 2. Pages liste : événement et catégorie

`/evenements/30-ans-pokemon.html` contient un bloc avec `productsData` (5 produits, `pagination.totalProductsCount: 5`) et un second bloc de recommandation de 6 autres produits Pokémon. Chaque item a `common.title`, `common.URL.canonical`, `stock.webStore`, `price.valueWithTax`. Le parseur garde les items dont le titre ou l'URL contient « 30e anniversaire », et dédoublonne par id.

Les 5 articles, tous `UNAVAILABLE` le 2026-09-20 :

| Prix | Produit | EAN |
|---|---|---|
| 14,99 € | 2 Boosters Evoli Pokémon 30e Anniversaire | |
| 29,99 € | Coffret 4 Boosters Nymphali EX Pokémon 30e Anniversaire | |
| 29,99 € | Coffret 4 Boosters Amphinobi EX Pokémon 30e Anniversaire | |
| 32,99 € | Coffret Collection poster Pokémon 30e Anniversaire | |
| 64,99 € | Coffret Dresseur d'élite Pokémon 30e Anniversaire | 0196214144835 |

Un nouveau produit sur la page événement = un id absent de `state.json`, d'où la notification basse priorité.

`/cartes-a-collectionner-pokemon.html` a la même structure (8 items dont 6 en stock) et sert de fixture positive.

## 3. API AJAX Proximis

Format lu dans `rbs-change-app.js` (`RbsChange.AjaxAPI`) et vérifié avec curl :

- `POST https://www.lagranderecre.fr/ajax.V1.php/fr_FR/<action>`
- En-têtes : `Content-Type: application/json`, `X-HTTP-Method-Override: GET` (la méthode logique ; tout part en POST)
- Corps : `{"websiteId":100052,"sectionId":103089,"pageId":100312,"data":{...},"referer":"<url de la page>"}` plus les paramètres de contexte (`dataSets`, `dataSetNames`, `URLFormats`, `pagination`)
- **Sans cookie, sans jeton, sans captcha** : les appels passent depuis un curl vierge.

### Disponibilité par magasin : `Rbs/Storeshipping/Store/`

C'est l'API de la modale « Consulter la disponibilité en magasin » (directive `rbsStoreshippingProductLocator`, fonction `launchSearch`, dans `blocks.min.js`). Corps observé :

```json
{"data": {"search": {"address": null, "country": null,
                     "coordinates": {"latitude": 48.8566, "longitude": 2.3522},
                     "processId": 0, "storeId": null, "useAsDefault": false},
          "skuQuantities": {"0196214144835": 1},
          "forReservation": true, "forPickUp": false, "allowSelect": false},
 "URLFormats": "canonical", "dataSetNames": "address,coordinates,hoursSummary"}
```

Le site envoie `useAsDefault: true` ; on envoie `false` pour ne pas modifier de magasin par défaut.

Réponse : `items[]` = **uniquement les magasins qui ont le produit** (le gabarit de la modale titre la liste « Disponible dans les magasins »). Chaque item a `common.id`, `common.title`, `address.fields.zipCode` / `.locality`, et `storeShipping.hasStoreStock: true`. `pagination.count` donne le nombre.

Résultats obtenus :

| Produit | Centre | Magasins avec stock |
|---|---|---|
| Académie de Combat (en stock partout) | Paris | 22 |
| Académie | Rouen | 7 |
| Académie | Rennes | 1 (Redon) |
| Académie | Brest | 0 |
| Académie | Caen | 1 |
| **Coffret Dresseur d'élite** (indispo web) | Paris | **11** : Italie 2, Porte des Lilas, Passy, Villeneuve-la-Garenne, Courbevoie, Rosny-sous-Bois, Carré Sénart, Buchelay, Vernon, Essômes-sur-Marne, Évreux |

Donc le coffret est en rayon dans 11 magasins alors que le web est en rupture. C'est exactement l'information que le radar doit remonter.

Le rayon de recherche est fixé côté serveur (`stock.storeLocatorDistance: "100kilometers"` dans le bloc produit), le client ne l'envoie pas. La directive prévoit aussi un mode `search.storeId` (recherche centrée sur un magasin), pas encore testé.

### ⚠️ Incident : 502 sur cet endpoint

Après une rafale d'environ 14 appels rapprochés (quelques secondes d'écart), `Rbs/Storeshipping/Store/` répond **502 Bad Gateway** (136 octets, en 0,14 s) à toutes les requêtes, y compris la charge utile qui passait juste avant, y compris depuis le navigateur avec une session normale. Toujours 502 après 45 s, puis après environ 15 minutes (un seul appel de contrôle). Les pages HTML, la page d'accueil et l'API `Rbs/Storelocator/Store/` répondent 200 pendant ce temps.

Deux lectures possibles, impossibles à départager depuis une seule IP : panne du service en amont, ou limitation par IP sur ce chemin précis. Ce n'est pas un captcha ni un 403, mais **c'est traité comme un signal de blocage** : plus aucun test en rafale, un seul appel de contrôle plus tard dans la soirée, et dans le radar une cadence prudente (3 s minimum entre deux appels API, backoff exponentiel sur 502, intervalle porté à 5 min tant que ça dure). Un 502 donne `INCONNU` et ne touche jamais au dernier statut connu.

Si l'endpoint reste en 502 durablement, le radar continue de surveiller le stock web (indépendant), et le stock magasin restera `INCONNU` avec un voyant dans l'interface.

### Liste des magasins

Les pages `/magasins/region/<slug>/` (`ile-de-france`, `normandie`, `bretagne`) portent `window.__change['6'].storesData` rendu serveur, avec `common.id`, `common.code`, `common.title`, `address.fields`, `coordinates`, `allow.allowPickUp` / `allowReservation`. Extrait par `tools/build_stores.py` dans `stores.yaml` : **34 magasins**, tous surveillés.

- Île-de-France (25) : Paris Poissonnière, La Boétie, Italie 2, Alésia, Beaugrenelle, Passy, Barbès, Porte des Lilas ; Levallois-Perret, Courbevoie, Villeneuve-la-Garenne, Rosny-sous-Bois, Arcueil, Créteil, Belle Épine (Thiais), Bry-sur-Marne, Montesson, Buchelay, Herblay, L'Isle-Adam, Évry 2, Claye-Souilly, Montévrain, Carré Sénart (Lieusaint), Provins.
- Normandie (6) : Rouen, Caen (Rots), Touques, Agneaux, Évreux, Vernon.
- Bretagne (3) : Redon, Lorient, Quimper.

L'API `Rbs/Storelocator/Store/` (`data: {coordinates, distance: "100kilometers", commercialSign: 100475, currentStoreId: 0, distanceUnit: "kilometers"}`, `pagination: "0,50"`) renvoie aussi la liste sans filtre de stock, mais les pages région suffisent.

### Autres endpoints vus, inutiles ici

`Rbs/Storeshipping/Store/Default` (magasin courant, lié au cookie de session), `Rbs/Commerce/Cart`, `Rbs/Review/ReviewsForTarget/...`, `Rbs/Geo/Phone/...`, `Rbs/Commerce/Availability/AlertSubscription` (l'alerte email du site, hors périmètre).

## 4. Anti-bot et politesse

- reCAPTCHA v2 présent dans le formulaire d'ajout au panier (`.g-recaptcha`, clé publique dans le DOM). Hors périmètre : on n'ajoute rien au panier, on ne se connecte pas.
- Aucun challenge sur les GET de pages ni sur les API testées. Bannière cookies Didomi sans effet sur le HTML brut.
- Le seul signal rencontré est le 502 décrit plus haut, provoqué par ma propre rafale de tests. Règle retenue : jamais de proxy ni de VPN, cadence lente, backoff, et si un blocage arrive quand même, intervalle à 5 min et attente.

## 5. Ce qu'on reprend de changedetection.io

Lecture du dépôt `dgtlmoon/changedetection.io`, en particulier `processors/restock_diff/processor.py` :

- **Hiérarchie de signaux** : il lit d'abord les métadonnées (schema.org `offers.availability`, microdata) puis vérifie par le texte rendu, avec ce commentaire dans le code : « Very often websites will lie about availability in metadata ». On fait pareil : la décision `DISPONIBLE_WEB` exige à la fois `stock.webStore.available` **et** un bouton d'achat non désactivé (`shippingCategories.disabled == false`). En repli Playwright, le libellé `.stock-availability` sert de troisième contrôle.
- **État inconnu explicite** : il n'assimile jamais « pas de signal » à « en stock ». Chez nous, tout échec donne `INCONNU`.
- **Notifier sur transition seulement** (mode `in_stock_only`) : c'est notre règle, avec en plus la confirmation à 10 s.
- **Suivi du prix** : il notifie sur changement de prix avec seuils. On garde le prix dans `state.json` et on l'affiche dans l'interface, mais un changement de prix ne déclenche pas de notif (hors périmètre).
- **Erreur par surveillance** dans l'interface, compteur d'échecs : repris tel quel dans le tableau de l'UI (dernière erreur, nombre de cycles en échec).
- **Bouton « Recheck now »** et file d'attente : notre `POST /api/refresh` avec un cooldown, pour que le bouton ne permette pas de contourner le plancher de 120 s.
- **Deux backends** (HTTP rapide, puis navigateur) : même découpage, httpx d'abord, Playwright en repli.
- **Pas repris** : les proxys tournants (Bright Data, Oxylabs), volontairement, et Apprise puisque ntfy suffit.

## 6. Méthode retenue pour le radar

1. **Stock web** : GET de chaque fiche avec httpx, parse de `window.__change`, statut `DISPONIBLE_WEB` si `stock.webStore.available` est vrai **et** `cartBox.shippingCategories.disabled` est faux.
2. **Stock magasin** : POST `Rbs/Storeshipping/Store/` par produit et par centre régional (Paris, Rouen, Caen, Rennes, Quimper), résultats dédoublonnés par id et filtrés sur `stores.yaml`. Statut `DISPONIBLE_MAGASIN` avec la liste des magasins. Le mode `search.storeId` sera testé une fois, gentiment, pour couvrir les magasins hors rayon des centres.
3. **Nouveaux produits** : GET de la page événement, ids inconnus de `state.json` → notification basse priorité.
4. **Repli Playwright** : `--fallback`, ou automatique après deux échecs de parse ; même parseur appliqué à `window.__change['6']` lu par `page.evaluate`. Sert aussi à produire `debug/`.
5. Un échec réseau, un 502 ou un parse raté donnent `INCONNU`, jamais `DISPONIBLE`.
