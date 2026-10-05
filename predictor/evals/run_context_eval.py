"""Eval script — mesure la qualité de l'extraction d'absents par news_agent.

Protocole :
  1. Charge jusqu'à 30 matchs passés avec home_absent_players ou away_absent_players non null.
  2. Pour chaque équipe, appelle search_team_context() sur la date du match.
  3. Compare les noms de joueurs extraits vs les noms stockés en DB (vérité terrain).
  4. Calcule précision, rappel, F1 au niveau des noms (exact match + partial match).
  5. Écrit le rapport JSON dans evals/results/context_eval_<timestamp>.json.

Usage :
  ANTHROPIC_API_KEY=... POSTGRES_URL=... python -m predictor.evals.run_context_eval
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as script from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import structlog

log = structlog.get_logger()

_MAX_SAMPLES = 30
_RESULTS_DIR = Path(__file__).parent / "results"


def _name_tokens(name: str) -> set[str]:
    return {t.lower() for t in name.split() if len(t) > 2}


def _match_names(predicted: list[str], ground_truth: list[str]) -> tuple[float, float, float]:
    """Précision, rappel, F1 sur les noms de joueurs (partial token match)."""
    if not ground_truth:
        return (1.0, 1.0, 1.0) if not predicted else (0.0, 1.0, 0.0)
    if not predicted:
        return (1.0, 0.0, 0.0)

    tp = sum(
        1 for p in predicted
        if any(_name_tokens(p) & _name_tokens(g) for g in ground_truth)
    )
    fp = len(predicted) - tp
    fn = sum(
        1 for g in ground_truth
        if not any(_name_tokens(g) & _name_tokens(p) for p in predicted)
    )

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return round(precision, 3), round(recall, 3), round(f1, 3)


def run_eval(anthropic_api_key: str, postgres_url: str) -> dict:
    import psycopg2
    import psycopg2.extras

    from src.ingestion.news_agent import search_team_context

    conn = psycopg2.connect(postgres_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """
        SELECT f.id, f.sport, f.home_team_name, f.away_team_name, f.match_date,
               mc.home_absent_players, mc.away_absent_players
        FROM fixtures f
        JOIN match_context mc ON mc.fixture_id = f.id
        WHERE (mc.home_absent_players IS NOT NULL OR mc.away_absent_players IS NOT NULL)
          AND f.match_date < NOW()
        ORDER BY f.match_date DESC
        LIMIT %s
        """,
        (_MAX_SAMPLES,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    log.info("context_eval.start", samples=len(rows))

    results = []
    precision_list: list[float] = []
    recall_list:    list[float] = []
    f1_list:        list[float] = []

    for row in rows:
        fixture_id = row["id"]
        sport      = row["sport"]
        match_date = row["match_date"]
        if match_date.tzinfo is None:
            match_date = match_date.replace(tzinfo=timezone.utc)

        for side in ("home", "away"):
            team_name = row[f"{side}_team_name"]
            opp_name  = row["away_team_name" if side == "home" else "home_team_name"]
            stored    = row.get(f"{side}_absent_players") or []
            if isinstance(stored, str):
                stored = json.loads(stored)
            ground_truth_names = [p["name"] for p in stored if isinstance(p, dict) and p.get("name")]

            if not ground_truth_names:
                continue

            extracted = search_team_context(anthropic_api_key, team_name, opp_name, match_date, sport)
            predicted_names = [p["name"] for p in extracted.get("absent_players", [])]

            precision, recall, f1 = _match_names(predicted_names, ground_truth_names)
            precision_list.append(precision)
            recall_list.append(recall)
            f1_list.append(f1)

            results.append({
                "fixture_id":       fixture_id,
                "team":             team_name,
                "match_date":       match_date.isoformat(),
                "ground_truth":     ground_truth_names,
                "predicted":        predicted_names,
                "precision":        precision,
                "recall":           recall,
                "f1":               f1,
            })

            log.info(
                "context_eval.sample",
                team=team_name, precision=precision, recall=recall, f1=f1,
                gt_n=len(ground_truth_names), pred_n=len(predicted_names),
            )

    def _mean(lst: list[float]) -> float:
        return round(sum(lst) / len(lst), 3) if lst else 0.0

    report = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "n_samples":       len(results),
        "mean_precision":  _mean(precision_list),
        "mean_recall":     _mean(recall_list),
        "mean_f1":         _mean(f1_list),
        "samples":         results,
    }

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = _RESULTS_DIR / f"context_eval_{ts}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    log.info(
        "context_eval.done",
        n=len(results),
        mean_precision=report["mean_precision"],
        mean_recall=report["mean_recall"],
        mean_f1=report["mean_f1"],
        report=str(out_path),
    )
    return report


if __name__ == "__main__":
    api_key     = os.environ.get("ANTHROPIC_API_KEY", "")
    postgres_url = os.environ.get("POSTGRES_URL", "")

    if not api_key or not postgres_url:
        print("Usage: ANTHROPIC_API_KEY=... POSTGRES_URL=... python -m predictor.evals.run_context_eval")
        sys.exit(1)

    run_eval(api_key, postgres_url)
