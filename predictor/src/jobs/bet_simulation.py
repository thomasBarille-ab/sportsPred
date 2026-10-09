"""Job de simulation de paris : calcule l'EV pour chaque prédiction fraîche
et insère dans bet_simulations si EV > seuil.

EV% = (model_prob × decimal_odds) - 1

Une seule ligne par fixture (UNIQUE constraint). On bet sur l'outcome
avec le meilleur EV parmi ceux au-dessus du seuil.
"""

from __future__ import annotations

import structlog

from ..db import session

log = structlog.get_logger()

_MIN_EV_PCT = 0.05  # seuil minimal pour créer une simulation (5 %)
_DISCORD_EV_THRESHOLD = 0.08  # EV >= 8% → alerte Discord


def _best_ev(
    sport: str,
    p_home: float,
    p_draw: float | None,
    p_away: float,
    odds_home: float | None,
    odds_draw: float | None,
    odds_away: float | None,
) -> tuple[str, float, float] | None:
    """Retourne (bet_outcome, odds_taken, ev_pct) si l'outcome prédit par le modèle a un EV > seuil.

    Le pari ne peut JAMAIS aller contre la prédiction du modèle : on calcule l'EV
    uniquement sur l'outcome le plus probable selon les probas prédites.
    """
    outcomes = [("home", p_home, odds_home)]
    if sport in ("ligue1", "france_nt") and p_draw is not None:
        outcomes.append(("draw", p_draw, odds_draw))
    outcomes.append(("away", p_away, odds_away))

    predicted = max(outcomes, key=lambda x: x[1])
    outcome, prob, odds = predicted
    if not odds or odds <= 1:
        return None

    ev = prob * odds - 1
    if ev <= _MIN_EV_PCT:
        return None
    return outcome, odds, ev


def _fetch_candidates() -> list[dict]:
    return session.fetch_all(
        """
        SELECT DISTINCT ON (p.fixture_id)
               p.id              AS prediction_id,
               p.fixture_id,
               f.sport,
               p.prob_home_win,
               p.prob_draw,
               p.prob_away_win,
               mo.bookmaker,
               mo.odds_home,
               mo.odds_draw,
               mo.odds_away
        FROM predictions p
        JOIN fixtures f ON f.id = p.fixture_id
        JOIN match_odds mo ON mo.fixture_id = p.fixture_id
        LEFT JOIN bet_simulations bs ON bs.fixture_id = p.fixture_id
        WHERE bs.id IS NULL
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date >= NOW() - INTERVAL '48 hours'
        ORDER BY p.fixture_id,
          CASE WHEN mo.bookmaker = 'pinnacle' THEN 0 ELSE 1 END,
          mo.fetched_at DESC
        """
    )


def _send_discord_alert(webhook_url: str, row: dict, bet_outcome: str, odds: float, ev_pct: float) -> None:
    """Envoie une alerte Discord pour un value bet significatif."""
    import httpx
    _SPORT_LABELS = {"ligue1": "⚽ Ligue 1", "france_nt": "⚽ France NT", "nba": "🏀 NBA"}
    sport_label = _SPORT_LABELS.get(row["sport"], row["sport"])
    outcome_label = {"home": "Victoire domicile", "draw": "Match nul", "away": "Victoire extérieur"}.get(bet_outcome, bet_outcome)
    content = (
        f"**Value Bet détecté** — {sport_label}\n"
        f"Fixture #{row['fixture_id']} · {outcome_label}\n"
        f"Cote : **{odds:.2f}** · EV : **{ev_pct:+.1%}** · Bookmaker : {row['bookmaker']}"
    )
    try:
        httpx.post(webhook_url, json={"content": content}, timeout=5)
    except Exception as exc:
        log.warning("bet_simulation.discord_error", error=str(exc))


def run_bet_simulation(discord_webhook_url: str = "") -> dict:
    """Génère les simulations de paris pour les prédictions des 48 dernières heures."""
    return _run(discord_webhook_url)


def _run(discord_webhook_url: str = "") -> dict:
    rows = _fetch_candidates()

    log.info("bet_simulation.start", candidats=len(rows))

    n_simulated = n_value_bets = 0

    for row in rows:
        result = _best_ev(
            sport=row["sport"],
            p_home=float(row["prob_home_win"]),
            p_draw=float(row["prob_draw"]) if row["prob_draw"] is not None else None,
            p_away=float(row["prob_away_win"]),
            odds_home=row["odds_home"],
            odds_draw=row["odds_draw"],
            odds_away=row["odds_away"],
        )

        if result is None:
            continue

        bet_outcome, odds_taken, ev_pct = result

        try:
            session.execute(
                """
                INSERT INTO bet_simulations
                  (fixture_id, prediction_id, bookmaker, market,
                   odds_taken, ev_pct, stake_units, bet_outcome)
                VALUES (%s,%s,%s,'h2h',%s,%s,1.0,%s)
                ON CONFLICT (fixture_id) DO NOTHING
                """,
                (
                    row["fixture_id"],
                    row["prediction_id"],
                    row["bookmaker"],
                    odds_taken,
                    round(ev_pct, 6),
                    bet_outcome,
                ),
            )
            n_simulated += 1
            if ev_pct > 0:
                n_value_bets += 1
                log.info(
                    "bet_simulation.value_bet",
                    fixture_id=row["fixture_id"],
                    sport=row["sport"],
                    outcome=bet_outcome,
                    odds=round(odds_taken, 2),
                    ev_pct=f"{ev_pct:.1%}",
                    bookmaker=row["bookmaker"],
                )
                if discord_webhook_url and ev_pct >= _DISCORD_EV_THRESHOLD:
                    _send_discord_alert(discord_webhook_url, row, bet_outcome, odds_taken, ev_pct)
        except Exception as exc:
            log.error("bet_simulation.insert_error",
                      fixture_id=row["fixture_id"], error=str(exc))

    log.info(
        "bet_simulation.done",
        simulated=n_simulated,
        value_bets=n_value_bets,
    )
    return {"simulated": n_simulated, "value_bets": n_value_bets}
