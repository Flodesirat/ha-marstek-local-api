# AGENTS.md — repères pour travailler sur ce dépôt

Ce fichier sert de point d'entrée rapide. Il ne duplique pas la doc : il dit où
la trouver, et fixe la façon de travailler.

## Où trouver quoi

| Besoin | Où |
|--------|----|
| **Tâches restantes, bugs, idées** | GitHub Issues (`gh issue list`) — voir [Tickets](#tickets--issues). Pas de fichier TODO dans le dépôt. |
| Vue d'ensemble, installation, entités, services | [README.md](README.md) |
| Problèmes connus (firmware, limites de l'API) | [README.md](README.md), section « API maturity & known issues » |
| Architecture, flux de données, choix de conception | [docs/DESIGN.md](docs/DESIGN.md) |
| Spécification de l'API locale Marstek | [docs/Marstek_Device_Open_API_EN_.Rev1.0.pdf](docs/Marstek_Device_Open_API_EN_.Rev1.0.pdf) |
| Outils CLI hors HA (diagnostic, capture de fixtures, qualité de com.) | `test/` — documentés dans le README, section « Standalone tools » |
| CI, beta et release | `.github/workflows/` — voir [Versioning et releases](#versioning-et-releases) |

## Structure du dépôt

```
custom_components/marstek_local_api/   Code de l'intégration (api, coordinator, sensor, config_flow…)
tests/                                 Tests unitaires (pytest)
tests/fixtures/<model>_fw<version>/    Réponses JSON brutes capturées sur de vrais appareils
tests_integration/                     Tests d'intégration (setup HA, états des entités)
test/                                  Outils CLI autonomes (test_tool.py, capture_fixtures.py, comm_quality.py, mping.py)
tools/                                 Ancien outil de release (release.py, format rc) — obsolète, ne pas utiliser
docs/                                  Design + spec de l'API
.github/workflows/                     tests, validate (HACS/hassfest), auto-beta, release
```

## Façon de travailler

0. **Quand on te demande de faire une tâche**, avant tout développement :
   1. Vérifier qu'il n'existe pas de ticket similaire, y compris fermé :
      `gh issue list --repo Flodesirat/ha-marstek-local-api --state all --search "<mots-clés>"`.
   2. **Pas de ticket similaire** : créer le ticket (voir
      [Créer un ticket](#procédure-pour-créer-un-ticket)) avec le label adapté
      (`bug`, `enhancement`, `documentation`…).
   3. **Ticket similaire trouvé** : en informer l'utilisateur, charger son
      contexte (`gh issue view <n> --comments`) et, si nécessaire, le mettre à
      jour (description, périmètre, critères).
1. **Une tâche = un ticket = un lot cohérent.**
2. **Suivre les conventions déjà en place** plutôt que d'en introduire de
   nouvelles : lire le code voisin (`api.py`, `coordinator.py`, `sensor.py`…)
   et [docs/DESIGN.md](docs/DESIGN.md) avant d'écrire du code.
3. **Toute amélioration ou correction doit être testée** :
   - `python -m pytest tests/` et `python -m pytest tests_integration/` verts ;
     la CI exige une couverture ≥ 90 % sur `custom_components/marstek_local_api`.
   - Un bug corrigé = un test de non-régression.
   - Ce qui touche au scaling ou aux champs des capteurs se teste contre les
     fixtures réelles de `tests/fixtures/` (en capturer de nouvelles avec
     `test/capture_fixtures.py` si un modèle/firmware manque).
   - Si les champs de `sensor.py` changent, garder les mocks de
     `test/test_tool.py` synchronisés (un test le vérifie).
4. **Toute amélioration ou correction doit être documentée** : mettre à jour
   le README (entités, services, problèmes connus) et/ou
   [docs/DESIGN.md](docs/DESIGN.md) si le changement impacte le fonctionnel ou
   l'architecture.
5. **Commits** : en anglais, à l'impératif (« Fix… », « Add… »), un commit par
   lot, avec le trailer `Co-Authored-By` attendu. Référencer le ticket si
   pertinent (`Fixes #<n>`).
6. **En terminant une tâche**, fermer son ticket (`gh issue close <n>`) si le
   commit ne le ferme pas déjà.

## Versioning et releases

La version vit uniquement dans `custom_components/marstek_local_api/manifest.json`
et est gérée par la CI (détail dans le README, section « Releases ») :

- **Beta automatique** : chaque push sur `master` dont le workflow `Tests`
  passe déclenche `Auto Beta Release`, qui incrémente la version
  (`1.3.3b1` → `1.3.3b2`, ou `1.3.2` → `1.3.3b1` depuis une stable), committe
  `Release <version> [skip ci]`, tague `v<version>` et publie une pre-release.
- **Stable manuelle** : *Actions → Release → Run workflow* avec la version
  cible (ex. `1.3.3`). Ne la déclencher que sur demande de l'utilisateur.

Règles qui en découlent :

- **Ne jamais modifier la version de `manifest.json` à la main**, ni créer de
  tag `v*` localement.
- Le bot pousse un commit après chaque push réussi : faire `git pull --rebase`
  avant de committer à nouveau sur `master`.
- Ne pas utiliser `tools/release.py` (ancien flux `rc`, incompatible avec le
  format `bN` actuel).

## Tickets / Issues

Les tickets de ce projet sont gérés via **GitHub Issues**, pas dans le dépôt local.

- Dépôt : `Flodesirat/ha-marstek-local-api`
- URL : https://github.com/Flodesirat/ha-marstek-local-api/issues
- Labels disponibles : `bug`, `enhancement`, `documentation`, `question`, `good first issue`, `help wanted`, `invalid`, `duplicate`, `wontfix`

### Procédure pour lister/consulter les tickets

Utiliser `gh` (GitHub CLI) si disponible :

```bash
gh issue list --repo Flodesirat/ha-marstek-local-api --state all
gh issue view <numéro> --repo Flodesirat/ha-marstek-local-api
```

Si `gh` n'est pas installé et qu'aucune installation via `sudo apt` n'est possible (pas de mot de passe sudo dans ce contexte), l'installer en local sans sudo :

```bash
curl -sL https://api.github.com/repos/cli/cli/releases/latest \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print([a['browser_download_url'] for a in d['assets'] if 'linux_amd64.tar.gz' in a['name']][0])"
# télécharger l'URL obtenue, extraire, puis copier bin/gh vers ~/.local/bin/gh
```

En lecture seule (lister/consulter des issues publiques), l'API REST sans authentification fonctionne aussi :

```bash
curl -s "https://api.github.com/repos/Flodesirat/ha-marstek-local-api/issues?state=all&per_page=100"
```

### Procédure pour créer un ticket

1. S'assurer que `gh` est authentifié : `gh auth status`.
2. Si non authentifié, demander à l'utilisateur un Personal Access Token (scope `repo`, créé sur https://github.com/settings/tokens/new) et l'utiliser via la variable d'environnement `GH_TOKEN` (ne pas utiliser `gh auth login --with-token`, qui peut exiger le scope `read:org` en trop ; `GH_TOKEN=<token> gh <commande>` suffit).
3. Créer le ticket :

```bash
GH_TOKEN="<token>" gh issue create --repo Flodesirat/ha-marstek-local-api \
  --title "..." \
  --label "enhancement" \
  --body "..."
```

**Important** : ne jamais committer de token dans le dépôt ni le laisser en clair dans un fichier. Une fois utilisé, l'utilisateur doit envisager de le révoquer si la session/conversation où il a été partagé n'est pas de confiance.
