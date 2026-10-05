# Sports Predictor

ML pipeline for predicting football (Ligue 1, France NT) and NBA match outcomes, with daily automated inference, market comparison, and a Next.js dashboard.

## Architecture

```
sports-predictor/
├── predictor/          # Python service — ML, ingestion, scheduling
│   └── src/
│       ├── db/         # Postgres pool, migration runner, SQL files
│       ├── ingestion/  # Data providers (football-data.org, balldontlie, API-Football)
│       ├── features/   # Feature engineering (Elo, Dixon-Coles, rolling stats)
│       ├── training/   # XGBoost training, champion/challenger promotion
│       ├── scoring/    # Post-match metrics (Brier, log-loss, accuracy)
│       ├── jobs/       # Orchestrated jobs: ingest, predict, evaluate, retrain
│       ├── summaries/  # LLM agent analysis (Claude)
│       └── scheduler.py
├── dashboard/          # Next.js 14 App Router — visualization
└── docker-compose.sports.yml
```

## ML Pipeline

**Features** are built in walk-forward fashion — no future data leaks into any feature at training time. Elo ratings and Dixon-Coles parameters are computed on a rolling basis using only matches prior to each sample.

**Training** uses XGBoost (multi-class softprob for football, binary for NBA). The dataset is split chronologically 85/15 (train/holdout). After XGBoost, an isotonic regression calibrator is fitted on holdout probabilities per class to correct for systematic over/under-confidence.

**Champion/challenger** promotion: after each weekly retrain, the challenger's holdout Brier scores are tested against the current production model using a paired bootstrap (1 000 resamples, 95 % CI). The challenger is only promoted if the lower bound of the CI is strictly positive (challenger strictly better). If holdout samples are insufficient (< 50), falls back to a fixed 0.002 threshold.

**No-odds variant**: every retrain also produces a `no_odds` variant that excludes implied-probability features derived from bookmaker odds. This variant is never promoted to production; it serves as a reference to quantify how much of the model's edge comes from market signal vs. structural features.

## Daily Schedule (UTC)

| Time | Job |
|------|-----|
| 06:00 | Ingest fixtures (Ligue 1, NBA, France NT) |
| 06:30 | Ingest opening odds (The Odds API) |
| 07:00 | Generate predictions + Claude explanations |
| 07:10 | Bet simulation (EV filter ≥ 5%) |
| 08:00 | Evaluate predictions (score settled matches) |
| 09:00 | Agent analysis (Claude tool-use, failure patterns) |
| 10:00 | Daily summary |
| 13:00 | Ingest closing odds (proxy H-1h) |
| 18:00 | Pre-match context agent (injuries, weather) |
| Mon 03:00 | Weekly retrain (standard + no-odds variants) |

## Running locally

```bash
docker compose -f docker-compose.sports.yml up -d
```

Migrations and backfills apply automatically on predictor startup.

## Tests

```bash
cd predictor && python -m pytest tests/ -v
```
