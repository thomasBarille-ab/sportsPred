# CLAUDE.md — Sports Predictor

## Rôle de Claude sur ce projet

Claude aide à coder, débugger, analyser et rédiger les messages de commit.
**Claude ne fait jamais `git add`, `git commit` ni `git push`.**
Quand du code est prêt à committer, Claude propose le message de commit formaté, Thomas exécute.

---

## Architecture

```
sports-predictor/
├── predictor/          # Service Python — ML, ingestion, scheduling
│   └── src/
│       ├── db/         # Pool Postgres, runner de migrations, fichiers SQL
│       ├── ingestion/  # Providers de données (football-data.org, balldontlie)
│       ├── features/   # Feature engineering (ELO, Dixon-Coles, rolling stats)
│       ├── training/   # Entraînement XGBoost, champion/challenger
│       ├── scoring/    # Métriques post-match (Brier, log-loss, accuracy)
│       ├── jobs/       # Jobs orchestrés : ingest, predict, evaluate, retrain
│       ├── summaries/  # Résumés LLM (Ollama / Claude)
│       ├── scheduler.py
│       ├── trigger_server.py
│       ├── config.py
│       └── main.py
├── dashboard/          # Next.js 14 App Router — visualisation
│   └── src/
│       ├── app/        # Pages (/, /ligue1, /nba, /models, /logs, /chat)
│       ├── components/ # Composants réutilisables (charts, nav, etc.)
│       └── lib/db.ts   # Toutes les requêtes SQL (postgres.js)
├── roadmap/            # Notes privées (gitignored)
├── docker-compose.sports.yml
└── CLAUDE.md
```

**Services Docker :** `postgres-sports` | `predictor` | `sports-dashboard` | `ollama`

**Pipeline quotidien (UTC) :**
```
06:00 ingest → 07:00 predict → 08:00 evaluate → 09:00 summary
03:00 lundi  → retrain (champion/challenger)
```

---

## Règles de code

### Python (predictor/)

- Python 3.12, pas de `from __future__ import annotations` sauf si nécessaire
- Type hints sur toutes les fonctions publiques
- Pas de `print()` — utiliser `structlog.get_logger()`
- Pas de `except Exception: pass` — logguer ou reraise
- Les jobs retournent toujours un `dict` de métriques (pour `agent_logs`)
- Les features sont construites en **walk-forward** — jamais de données futures dans le calcul d'une feature

### TypeScript (dashboard/)

- TypeScript strict, pas de `any` sauf commentaire justificatif
- Named exports uniquement (pas de `export default`)
- Server Components par défaut, Client Component (`"use client"`) seulement si nécessaire
- Toutes les requêtes SQL dans `dashboard/src/lib/db.ts`, jamais inline dans les pages
- Les pages async font un `Promise.all([...])` groupé, pas de requêtes en cascade
- Chaque requête SQL qui peut échouer (nouvelle colonne, migration en cours) s'enveloppe dans `.catch(() => fallback)` côté page

### Général

- Pas de commentaires qui décrivent ce que le code fait — seulement le **pourquoi** si non-évident
- Pas de code mort commenté
- Pas de `TODO` laissé dans le code commité

---

## Migrations DB

- Chaque changement de schéma = nouveau fichier `predictor/src/db/migrations/NNN_description.sql`
- Numérotation séquentielle : `001`, `002`, `003`...
- Les migrations sont **idempotentes** (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`)
- Le runner `predictor/src/db/migrate.py` les applique automatiquement au démarrage
- **Ne jamais modifier** un fichier de migration déjà commité — créer un nouveau fichier

---

## Variables d'environnement

Toutes les variables sont déclarées dans :
1. `predictor/src/config.py` — dataclass `Settings` avec `load_settings()`
2. `docker-compose.sports.yml` — mapping vers les services
3. `.env.example` — template documenté (jamais de valeurs réelles)

Ajouter une variable = modifier les 3 fichiers.

---

## Git — Workflow et conventions

### Branches

```
master          → production (toujours déployable)
feat/<sujet>    → nouvelle fonctionnalité
fix/<sujet>     → correction de bug
chore/<sujet>   → infra, dépendances, config
```

Pas de branches longues — merge dès que la feature est testée en local.

### Conventional Commits

Format : `<type>(<scope>): <description impérative en minuscules>`

| Type | Quand |
|---|---|
| `feat` | Nouvelle fonctionnalité visible |
| `fix` | Correction de bug |
| `perf` | Amélioration de performance |
| `refactor` | Restructuration sans changement de comportement |
| `chore` | Dépendances, config, infra, CI |
| `docs` | Documentation uniquement |
| `test` | Ajout ou modification de tests |

**Scopes fréquents :**
`ml` · `ingest` · `predict` · `evaluate` · `features` · `db` · `dashboard` · `docker` · `auth` · `agent`

**Exemples valides :**
```
feat(dashboard): add matchday summary table on /ligue1
fix(db): include round in upsert fixture to prevent null on prod
chore(docker): add auto-migration runner on predictor startup
feat(ml): add dixon-coles implied probability features
fix(predict): catch getMatchdaySummary error when column missing
refactor(features): extract rolling stats into dedicated module
```

**À éviter :**
```
fix again          ← pas de type, pas de scope
fix vue            ← trop vague
add stuff          ← aucune information
WIP                ← ne jamais committer du WIP sur master
```

### Granularité des commits

- **1 commit = 1 changement logique cohérent**
- Une migration SQL + le code qui l'utilise = 1 commit
- Un nouveau job + son entrée dans le scheduler = 1 commit
- Un fix qui touche predictor ET dashboard = 1 commit si c'est le même bug

### Quand Claude propose un commit

Claude fournit le message formaté, Thomas exécute :
```bash
git add <fichiers spécifiques>
git commit -m "feat(scope): description"
```
Claude ne lance jamais ces commandes.

---

## Déploiement

Le projet tourne sur un serveur via Docker Compose + Cloudflare Tunnel.

**Déployer = rebuilder les images et redémarrer les containers :**
```bash
docker compose -f docker-compose.sports.yml build <service>
docker compose -f docker-compose.sports.yml up -d <service>
```

Les migrations et backfills s'appliquent **automatiquement** au démarrage du predictor.
Pas besoin de commandes manuelles post-déploiement.

---

## Tests

```bash
cd predictor && python -m pytest tests/ -v
```

Les tests couvrent : ingestion, features ML, métriques de scoring.
Avant tout PR ou commit important : lancer les tests et vérifier qu'ils passent.

---

## Choses à ne jamais faire

- `git add -A` ou `git add .` — toujours stager les fichiers explicitement
- Committer `.env` ou tout fichier contenant des clés API
- Modifier un fichier de migration déjà en prod
- Utiliser `AUTH_DISABLED=true` en production
- Hardcoder une URL, clé API ou mot de passe dans le code
- Laisser une prédiction rétroactive (générée après le match) dans la DB sans la labeller comme telle
