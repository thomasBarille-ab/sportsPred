"""Job d'évaluation : calcule les métriques pour les prédictions dont le
résultat est maintenant connu, et les écrit dans prediction_scores.
"""

from __future__ import annotations

import structlog

from ..db import session
from ..scoring.metrics import brier_score, outcome_from_scores, score_prediction

log = structlog.get_logger()


def _market_brier(
    fixture_id: int,
    home_score: int,
    away_score: int,
    sport: str,
) -> float | None:
    """Calcule le Brier des probabilités implicites Pinnacle (de-juiced) pour ce match.

    De-juicing : normalisation simple 1/odds. Retourne None si aucune cote disponible.
    """
    row = session.fetch_one(
        """
        SELECT odds_home, odds_draw, odds_away
        FROM match_odds
        WHERE fixture_id = %s
        ORDER BY CASE WHEN bookmaker = 'pinnacle' THEN 0 ELSE 1 END, fetched_at DESC
        LIMIT 1
        """,
        (fixture_id,),
    )
    if not row:
        return None
    try:
        oh = float(row["odds_home"] or 0)
        od = row["odds_draw"]
        oa = float(row["odds_away"] or 0)
        if oh <= 1 or oa <= 1:
            return None
        if od and float(od) > 1:
            od_f = float(od)
            total = 1 / oh + 1 / od_f + 1 / oa
            p_home = (1 / oh) / total
            p_draw = (1 / od_f) / total
            p_away = (1 / oa) / total
        else:
            total = 1 / oh + 1 / oa
            p_home = (1 / oh) / total
            p_draw = None
            p_away = (1 / oa) / total
        return brier_score(p_home, p_draw, p_away, outcome_from_scores(home_score, away_score, sport), sport)
    except (ZeroDivisionError, TypeError, ValueError):
        return None


def run_evaluate(sport: str) -> int:
    """Évalue toutes les prédictions non encore scorées dont le match est terminé."""
    rows = session.fetch_all(
        """
        SELECT p.id             AS prediction_id,
               p.fixture_id,
               p.prob_home_win,
               p.prob_draw,
               p.prob_away_win,
               p.predicted_outcome,
               r.home_score,
               r.away_score,
               f.home_team_name,
               f.away_team_name
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN results  r ON r.fixture_id = p.fixture_id
        LEFT JOIN prediction_scores ps ON ps.prediction_id = p.id
        WHERE f.sport = %s AND ps.id IS NULL
        """,
        (sport,),
    )

    log.info(
        "evaluate.start",
        sport=sport,
        prédictions_à_scorer=len(rows),
    )

    if not rows:
        log.info("evaluate.rien_à_faire", sport=sport,
                 reason="Aucune prédiction en attente de résultat")
        _settle_bets(sport)
        return 0

    n_scored = n_correct = n_wrong = 0

    for row in rows:
        try:
            scores = score_prediction(
                prob_home=row["prob_home_win"],
                prob_draw=row["prob_draw"],
                prob_away=row["prob_away_win"],
                predicted_outcome=row["predicted_outcome"],
                actual_home_score=row["home_score"],
                actual_away_score=row["away_score"],
                sport=sport,
            )
            mkt_brier = _market_brier(row["fixture_id"], row["home_score"], row["away_score"], sport)
            session.execute(
                """
                INSERT INTO prediction_scores
                  (prediction_id, fixture_id, brier_score, log_loss, is_correct, market_brier)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (prediction_id) DO NOTHING
                """,
                (
                    row["prediction_id"],
                    row["fixture_id"],
                    scores["brier_score"],
                    scores["log_loss"],
                    scores["is_correct"],
                    mkt_brier,
                ),
            )

            actual_outcome = outcome_from_scores(row["home_score"], row["away_score"], sport)
            correct = scores["is_correct"]
            verdict = "✓ CORRECT" if correct else "✗ RATÉ"
            n_scored += 1
            if correct: n_correct += 1
            else:       n_wrong  += 1

            log.info(
                "evaluate.résultat",
                match=f"{row['home_team_name']} vs {row['away_team_name']}",
                score=f"{row['home_score']}-{row['away_score']}",
                prédit=row["predicted_outcome"],
                réel=actual_outcome,
                verdict=verdict,
                brier=round(scores["brier_score"], 4),
            )

        except Exception as exc:
            log.error("evaluate.erreur",
                      prediction_id=row["prediction_id"],
                      match=f"{row.get('home_team_name','?')} vs {row.get('away_team_name','?')}",
                      erreur=str(exc))

    accuracy = n_correct / n_scored if n_scored else 0
    log.info(
        "evaluate.bilan",
        sport=sport,
        scorés=n_scored,
        corrects=n_correct,
        ratés=n_wrong,
        accuracy_batch=f"{accuracy:.0%}",
    )

    # ── Settlement des bet_simulations pending ────────────────────────────────
    _settle_bets(sport)

    return n_scored


def _settle_bets(sport: str) -> None:
    """Settle les bet_simulations pending dont le résultat est maintenant connu.
    Gère tous les markets (h2h, ou_total, btts, double_chance)."""
    pending = session.fetch_all(
        """
        SELECT bs.id, bs.fixture_id, bs.odds_taken, bs.stake_units,
               bs.market, bs.selection, bs.line,
               r.home_score, r.away_score, r.actual_outcome
        FROM bet_simulations bs
        JOIN fixtures f ON f.id = bs.fixture_id
        JOIN results r ON r.fixture_id = bs.fixture_id
        WHERE f.sport = %s AND bs.status = 'pending'
          AND bs.selection IS NOT NULL
        """,
        (sport,),
    )

    if not pending:
        return

    n_won = n_lost = n_void = 0
    for b in pending:
        verdict = _bet_verdict(b)
        if verdict is None:
            continue
        status, won = verdict

        if status == "void":
            pnl = 0.0
            n_void += 1
        elif won:
            pnl = b["stake_units"] * (b["odds_taken"] - 1)
            n_won += 1
        else:
            pnl = -b["stake_units"]
            n_lost += 1

        clv = _compute_h2h_clv(b) if b["market"] == "h2h" else None

        session.execute(
            """
            UPDATE bet_simulations
            SET status = %s, pnl_units = %s, clv = %s, settled_at = NOW()
            WHERE id = %s
            """,
            (status, round(pnl, 4), clv, b["id"]),
        )

    log.info("evaluate.bets_settled", sport=sport, won=n_won, lost=n_lost, void=n_void)


def _bet_verdict(b: dict) -> tuple[str, bool] | None:
    """Détermine (status, won) pour un pari. Retourne None si impossible à régler."""
    market = b["market"]
    selection = b["selection"]
    h = b["home_score"]
    a = b["away_score"]

    if market == "h2h":
        actual = b.get("actual_outcome")
        if not actual:
            return None
        won = actual == selection
        return ("won" if won else "lost", won)

    if market == "ou_total":
        line = float(b["line"])
        total = h + a
        if abs(total - line) < 1e-6:
            return ("void", False)
        over = total > line
        if selection == "over":
            return ("won" if over else "lost", over)
        if selection == "under":
            return ("won" if not over else "lost", not over)
        return None

    if market == "btts":
        yes = h >= 1 and a >= 1
        if selection == "yes":
            return ("won" if yes else "lost", yes)
        if selection == "no":
            return ("won" if not yes else "lost", not yes)
        return None

    if market == "double_chance":
        home_win, draw, away_win = h > a, h == a, h < a
        if selection == "1x":
            won = home_win or draw
        elif selection == "12":
            won = home_win or away_win
        elif selection == "x2":
            won = draw or away_win
        else:
            return None
        return ("won" if won else "lost", won)

    return None


def _compute_h2h_clv(b: dict) -> float | None:
    """CLV = (cote_prise / cote_clôture) - 1 — défini uniquement pour h2h."""
    closing_row = session.fetch_one(
        """
        SELECT odds_home, odds_draw, odds_away
        FROM match_odds
        WHERE fixture_id = %s AND closing = TRUE
        ORDER BY CASE WHEN bookmaker = 'pinnacle' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (b["fixture_id"],),
    )
    if not closing_row:
        return None
    col_map = {"home": "odds_home", "draw": "odds_draw", "away": "odds_away"}
    col = col_map.get(b["selection"])
    if not col:
        return None
    closing_odd = closing_row.get(col)
    if closing_odd and float(closing_odd) > 1:
        return round(float(b["odds_taken"]) / float(closing_odd) - 1, 4)
    return None
