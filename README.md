# Sports Predictor

Agent autonome de prédiction sportive pour la **Ligue 1** et la **NBA**.  
Il ingère les données, entraîne des modèles ML, génère des prédictions et produit un résumé quotidien via un LLM local.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  sports-dashboard                   │
│         Next.js 14 · App Router · Tailwind          │
│  / · /ligue1 · /nba · /models · /logs · /chat       │
└───────────────────┬─────────────────────────────────┘
                    │ REST (INTERNAL_API_TOKEN)
┌───────────────────▼─────────────────────────────────┐
│                   predictor                         │
│     Python · APScheduler · XGBoost · Dixon-Coles    │
│  ingest → features → train → predict → evaluate     │
└──────────┬────────────────────────┬─────────────────┘
           │ PostgreSQL             │ HTTP
┌──────────▼──────────┐   ┌────────▼────────┐
│   postgres-sports   │   │     ollama      │
│   PostgreSQL 16     │   │  llama3.2:3b    │
└─────────────────────┘   └─────────────────┘
```

Accès public via **Cloudflare Tunnel** (pas de port exposé en prod).

---

## Modèle de prédiction

```
  Données historiques (2 saisons)
            │
            ▼
  ┌─────────────────────────────────────────────┐
  │             Feature engineering             │
  │                                             │
  │  Elo         — force relative des équipes   │
  │  Dixon-Coles — probabilités de buts (L1)    │
  │  Forme       — 5/10 derniers matchs         │
  │  Repos       — jours depuis dernier match   │
  └──────────────────────┬──────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────┐
  │         Entraînement walk-forward           │
  │                                             │
  │  passé ──────────────────► présent          │
  │  [train]      [predict]   [update model]    │
  │                                             │
  │  Chaque ligne est prédite AVANT que         │
  │  le modèle la voit → pas de fuite           │
  └──────────────────────┬──────────────────────┘
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
  ┌──────────────────┐     ┌──────────────────────┐
  │  XGBoost L1      │     │  XGBoost NBA         │
  │  3 classes       │     │  2 classes           │
  │  home/draw/away  │     │  home/away           │
  └────────┬─────────┘     └──────────┬───────────┘
           └──────────┬───────────────┘
                      ▼
           Champion / Challenger
           (nouveau modèle promu
            seulement s'il fait
            mieux sur le holdout)
                      │
                      ▼
         Prédiction : probabilités
         ex. Domicile 52% · Nul 24% · Extérieur 24%
                      │
                      ▼
         Évaluation après le match
         Brier score · Log-loss · Accuracy
```

---

## Prérequis

- Docker Desktop ≥ 24
- Clé API gratuite [football-data.org](https://www.football-data.org/) (Ligue 1)
- Clé API gratuite [balldontlie.io](https://app.balldontlie.io) (NBA)

---

## Démarrage rapide

```bash
cp .env.example .env
# Renseigner FOOTBALL_DATA_API_KEY, BALLDONTLIE_API_KEY, POSTGRES_PASSWORD
# Générer INTERNAL_API_TOKEN : openssl rand -hex 32
# Ajouter AUTH_DISABLED=true pour le dev local

docker compose -f docker-compose.sports.yml --env-file .env up -d
```

Dashboard disponible sur **http://localhost:3000**.

Au premier démarrage, le predictor charge automatiquement l'historique des deux dernières saisons et entraîne les modèles.  
Ollama télécharge `llama3.2:3b` (~2 GB) en arrière-plan.

---

## Pipeline automatique (UTC)

| Heure | Job |
|---|---|
| 06:00 | Ingestion Ligue 1 + NBA |
| 07:00 | Génération des prédictions |
| 08:00 | Évaluation (Brier score, accuracy) |
| 09:00 | Résumé quotidien Ollama |
| lundi 03:00 | Réentraînement des modèles |

---

## Dashboard

| Page | Contenu |
|---|---|
| `/` | Vue d'ensemble des métriques |
| `/ligue1` · `/nba` | Prédictions à venir + historique |
| `/models` | Versions de modèles et performances |
| `/logs` | Historique des jobs |
| `/chat` | Chat avec l'agent — commandes `/ingest`, `/predict`, `/evaluate`, `/retrain`, `/summary` |

---

## Variables d'environnement

Voir `.env.example` pour la liste complète.  
Les variables obligatoires sont `POSTGRES_PASSWORD`, `FOOTBALL_DATA_API_KEY`, `BALLDONTLIE_API_KEY` et `INTERNAL_API_TOKEN`.

---

## Glossaire

**Brier score** — mesure la précision des probabilités prédites. Plus il est bas, mieux c'est. Un modèle qui dit toujours "33%/33%/33%" obtient 0.667 en Ligue 1 — c'est la baseline à battre. Un modèle parfait obtiendrait 0.

**Elo** — système de classement dynamique emprunté aux échecs. Chaque équipe a une note qui monte après une victoire et baisse après une défaite. L'écart de notes entre deux équipes sert de feature pour estimer leur force relative.

**Dixon-Coles** — modèle statistique qui prédit les probabilités de chaque score possible (0-0, 1-0, 1-1…) en modélisant les buts comme des événements de Poisson. Utilisé uniquement en Ligue 1 où le nul existe.

**Walk-forward** — méthode d'entraînement sans fuite de données : on entraîne le modèle uniquement sur le passé, on prédit le prochain match, puis on met à jour le modèle avec ce match avant de passer au suivant. Cela simule fidèlement les conditions réelles.

**Champion/Challenger** — à chaque réentraînement, le nouveau modèle (challenger) est comparé au modèle actuel (champion) sur les mêmes données de test. Le challenger ne remplace le champion que s'il fait mieux — évite de dégrader les prédictions.

**Holdout** — portion des données mise de côté et jamais vue pendant l'entraînement, utilisée uniquement pour mesurer les performances finales du modèle.

**Log-loss** — autre mesure de qualité des probabilités, qui pénalise fortement les erreurs confiantes (dire "90% victoire domicile" quand l'équipe perd).

---

## Déploiement en production

```bash
# Mettre AUTH_DISABLED=false et renseigner CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD
# Remplacer ports: par expose: dans docker-compose.sports.yml
git push prod master
```
