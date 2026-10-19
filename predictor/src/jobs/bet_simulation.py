"""Job de simulation de paris : calcule l'EV pour chaque prédiction fraîche
et insère dans bet_simulations.

EV% = (model_prob × decimal_odds) - 1

Deux passes, max 2 paris par match (1 h2h + 1 autre market):
  1. h2h : parie l'outcome prédit par le modèle (quel que soit l'EV).
  2. markets dérivés (O/U, BTTS, spreads) : au plus un pari par fixture —
     celui avec le meilleur EV, uniquement si EV > _MIN_EV_PCT.
"""

from __future__ import annotations

import structlog

from ..db import session

log = structlog.get_logger()

_MIN_EV_PCT = 0.05  # seuil minimal pour créer une simulation (5 %)


def _best_ev(
    sport: str,
    p_home: float,
    p_draw: float | None,
    p_away: float,
    odds_home: float | None,
    odds_draw: float | None,
    odds_away: float | None,
) -> tuple[str, float, float] | None:
    """Retourne (bet_outcome, odds_taken, ev_pct) pour l'outcome prédit par le modèle.

    Le pari suit toujours la prédiction (outcome le plus probable).
    Retourne None uniquement si aucune cote n'est disponible pour cet outcome.
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
    return outcome, odds, ev


def _fetch_candidates() -> list[dict]:
    """h2h : fixtures sans pari h2h simulé, avec cotes dispo."""
    return session.fetch_all(
        """
        SELECT DISTINCT ON (p.fixture_id)
               p.id              AS prediction_id,
               p.fixture_id,
               f.sport,
               f.home_team_name,
               f.away_team_name,
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
        LEFT JOIN bet_simulations bs
          ON bs.fixture_id = p.fixture_id AND bs.market = 'h2h'
        WHERE bs.id IS NULL
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date >= NOW() - INTERVAL '48 hours'
        ORDER BY p.fixture_id,
          CASE WHEN mo.bookmaker = 'pinnacle' THEN 0 ELSE 1 END,
          mo.fetched_at DESC
        """
    )


def _fetch_market_candidates() -> list[dict]:
    """Markets dérivés : pour chaque (fixture, market, selection, line) prédit,
    la meilleure cote dispo (Pinnacle en priorité). Exclut les fixtures qui ont
    déjà un pari non-h2h — on en veut max 1 par match."""
    return session.fetch_all(
        """
        WITH ranked AS (
          SELECT
            pm.fixture_id,
            pm.market,
            pm.selection,
            pm.line,
            pm.prob,
            mom.bookmaker,
            mom.odds,
            ROW_NUMBER() OVER (
              PARTITION BY pm.fixture_id, pm.market, pm.selection, pm.line
              ORDER BY CASE WHEN mom.bookmaker = 'pinnacle' THEN 0 ELSE 1 END,
                       mom.fetched_at DESC
            ) AS rn
          FROM predictions_markets pm
          JOIN match_odds_markets mom
            ON mom.fixture_id = pm.fixture_id
           AND mom.market     = pm.market
           AND mom.selection  = pm.selection
           AND mom.line       = pm.line
           AND mom.closing    = FALSE
          JOIN fixtures f ON f.id = pm.fixture_id
          WHERE NOT EXISTS (
                  SELECT 1 FROM bet_simulations bs
                  WHERE bs.fixture_id = pm.fixture_id
                    AND bs.market != 'h2h'
                )
            AND f.status IN ('SCHEDULED', 'TIMED')
            AND f.match_date >= NOW() - INTERVAL '48 hours'
        )
        SELECT fixture_id, market, selection, line, prob, bookmaker, odds
        FROM ranked
        WHERE rn = 1
        """
    )


def _send_discord_alert(webhook_url: str, row: dict, bet_outcome: str, odds: float, ev_pct: float) -> None:
    """Envoie une alerte Discord pour un value bet significatif."""
    import httpx
    home = row["home_team_name"]
    away = row["away_team_name"]
    pronos = {"home": home, "draw": "Match nul", "away": away}.get(bet_outcome, bet_outcome)
    content = (
        f"**{home} — {away}**\n"
        f"Prono : **{pronos}** · Cote : **{odds:.2f}**"
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
                  (fixture_id, prediction_id, bookmaker, market, selection, line,
                   odds_taken, ev_pct, stake_units, bet_outcome)
                VALUES (%s,%s,%s,'h2h',%s,0,%s,%s,1.0,%s)
                ON CONFLICT (fixture_id, market, selection, line) DO NOTHING
                """,
                (
                    row["fixture_id"],
                    row["prediction_id"],
                    row["bookmaker"],
                    bet_outcome,          # selection
                    odds_taken,
                    round(ev_pct, 6),
                    bet_outcome,          # bet_outcome legacy
                ),
            )
            n_simulated += 1
            if ev_pct > 0:
                n_value_bets += 1
            log.info(
                "bet_simulation.simulated",
                fixture_id=row["fixture_id"],
                sport=row["sport"],
                outcome=bet_outcome,
                odds=round(odds_taken, 2),
                ev_pct=f"{ev_pct:.1%}",
                bookmaker=row["bookmaker"],
            )
            if discord_webhook_url:
                _send_discord_alert(discord_webhook_url, row, bet_outcome, odds_taken, ev_pct)
        except Exception as exc:
            log.error("bet_simulation.insert_error",
                      fixture_id=row["fixture_id"], error=str(exc))

    # ── Passe 2 : markets dérivés — max 1 pari par fixture (meilleur EV) ─────
    market_rows = _fetch_market_candidates()
    log.info("bet_simulation.markets_candidates", candidats=len(market_rows))

    # Pour chaque fixture, on garde la (market, selection, line) avec l'EV le plus
    # haut parmi celles qui dépassent le seuil.
    best_by_fixture: dict[int, dict] = {}
    for row in market_rows:
        odds = float(row["odds"])
        prob = float(row["prob"])
        if odds <= 1:
            continue
        ev = prob * odds - 1
        if ev < _MIN_EV_PCT:
            continue
        fid = row["fixture_id"]
        current = best_by_fixture.get(fid)
        if current is None or ev > current["_ev"]:
            best_by_fixture[fid] = {**row, "_ev": ev, "_odds": odds}

    n_mkt_sim = 0
    for best in best_by_fixture.values():
        try:
            session.execute(
                """
                INSERT INTO bet_simulations
                  (fixture_id, prediction_id, bookmaker, market, selection, line,
                   odds_taken, ev_pct, stake_units, bet_outcome)
                VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, 1.0, NULL)
                ON CONFLICT (fixture_id, market, selection, line) DO NOTHING
                """,
                (
                    best["fixture_id"],
                    best["bookmaker"],
                    best["market"],
                    best["selection"],
                    best["line"],
                    best["_odds"],
                    round(best["_ev"], 6),
                ),
            )
            n_mkt_sim += 1
            log.info(
                "bet_simulation.market_simulated",
                fixture_id=best["fixture_id"],
                market=best["market"],
                selection=best["selection"],
                line=float(best["line"]),
                odds=round(best["_odds"], 2),
                ev_pct=f"{best['_ev']:.1%}",
                bookmaker=best["bookmaker"],
            )
        except Exception as exc:
            log.error("bet_simulation.market_insert_error",
                      fixture_id=best["fixture_id"], error=str(exc))

    log.info(
        "bet_simulation.done",
        simulated=n_simulated,
        value_bets=n_value_bets,
        market_simulated=n_mkt_sim,
    )
    return {
        "simulated": n_simulated + n_mkt_sim,
        "value_bets": n_value_bets + n_mkt_sim,
        "h2h_simulated": n_simulated,
        "market_simulated": n_mkt_sim,
    }
