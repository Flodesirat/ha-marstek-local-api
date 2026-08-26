# Tickets / Issues

Les tickets de ce projet sont gérés via **GitHub Issues**, pas dans le dépôt local (pas de fichier TODO).

- Dépôt : `Flodesirat/ha-marstek-local-api`
- URL : https://github.com/Flodesirat/ha-marstek-local-api/issues
- Labels disponibles : `bug`, `enhancement`, `documentation`, `question`, `good first issue`, `help wanted`, `invalid`, `duplicate`, `wontfix`

## Procédure pour lister/consulter les tickets

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

## Procédure pour créer un ticket

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
