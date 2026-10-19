"""Prédictions multi-markets pour Ligue 1 et NBA.

Ligue 1 : dérive O/U, BTTS, Double Chance de la matrice de scores Dixon-Coles.
NBA : baseline EWMA (off/def ratings) + Normal CDF pour O/U et point spreads.

Dans les deux cas, pour les markets avec line (O/U, spreads), on prédit
uniquement pour les lines effectivement offertes par un bookmaker dans
match_odds_markets. BTTS et DC n'ont pas de line (line=0 par convention)
et sont toujours prédits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import joblib
import structlog

from ..db import session
from ..features.nba_scoring import NBAScoringModel, fit_nba_scoring

log = structlog.get_logger()

_DEFAULT_HORIZON_HOURS = 48


def _upsert(
    fixture_id: int,
    market: str,
    selection: str,
    line: float,
    prob: float,
    model_source: str,
) -> None:
    session.execute(
        """
        INSERT INTO predictions_markets
          (fixture_id, market, selection, line, prob, model_source)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (fixture_id, market, selection, line) DO UPDATE
          SET prob = EXCLUDED.prob,
              model_source = EXCLUDED.model_source,
              created_at = NOW()
        """,
        (fixture_id, market, selection, line, prob, model_source),
    )


def _available_lines(fixture_id: int, market: str) -> list[float]:
    """Retourne les lines distinctes offertes pour ce (fixture, market)."""
    rows = session.fetch_all(
        """
        SELECT DISTINCT line
        FROM match_odds_markets
        WHERE fixture_id = %s AND market = %s
        """,
        (fixture_id, market),
    )
    return sorted({float(r["line"]) for r in rows})


def _available_spread_selections(fixture_id: int) -> list[tuple[str, float]]:
    """Retourne les (selection, line) distinctes offertes pour le market spreads."""
    rows = session.fetch_all(
        """
        SELECT DISTINCT selection, line
        FROM match_odds_markets
        WHERE fixture_id = %s AND market = 'spreads'
        """,
        (fixture_id,),
    )
    return [(r["selection"], float(r["line"])) for r in rows]


# ─── Ligue 1 (Dixon-Coles) ────────────────────────────────────────────────────

def _predict_ligue1(horizon_hours: int) -> tuple[int, int]:
    prod_model = session.fetch_one(
        "SELECT * FROM model_versions WHERE sport = 'ligue1' AND is_production = TRUE LIMIT 1",
    )
    if not prod_model:
        log.warning("predict_markets.no_ligue1_model")
        return 0, 0

    artifact = joblib.load(prod_model["model_path"])
    dc_model = artifact.get("dc_model")
    if not dc_model or not getattr(dc_model, "converged", False):
        log.warning("predict_markets.no_ligue1_dc_model")
        return 0, 0

    now = datetime.now(UTC)
    horizon = now + timedelta(hours=horizon_hours)
    fixtures = session.fetch_all(
        """
        SELECT f.id, f.home_team_id, f.away_team_id, f.home_team_name, f.away_team_name
        FROM fixtures f
        WHERE f.sport = 'ligue1'
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date BETWEEN %s AND %s
        """,
        (now, horizon),
    )

    n_predicted = 0
    for f in fixtures:
        home = f["home_team_id"]
        away = f["away_team_id"]
        try:
            # O/U : lines offertes par les bookmakers, fallback 2.5 si aucune
            ou_lines = _available_lines(f["id"], "ou_total") or [2.5]
            for line in ou_lines:
                p_over, p_under = dc_model.predict_over_under(home, away, line=line)
                _upsert(f["id"], "ou_total", "over",  line, float(p_over),  "dixon_coles")
                _upsert(f["id"], "ou_total", "under", line, float(p_under), "dixon_coles")
                n_predicted += 2

            # BTTS : pas de line
            p_yes, p_no = dc_model.predict_btts(home, away)
            _upsert(f["id"], "btts", "yes", 0.0, float(p_yes), "dixon_coles")
            _upsert(f["id"], "btts", "no",  0.0, float(p_no),  "dixon_coles")
            n_predicted += 2

            # Double Chance : pas de line, pas de bet simulation tant qu'on n'a pas d'odds
            p_1x, p_12, p_x2 = dc_model.predict_double_chance(home, away)
            _upsert(f["id"], "double_chance", "1x", 0.0, float(p_1x), "dixon_coles")
            _upsert(f["id"], "double_chance", "12", 0.0, float(p_12), "dixon_coles")
            _upsert(f["id"], "double_chance", "x2", 0.0, float(p_x2), "dixon_coles")
            n_predicted += 3
        except Exception as exc:
            log.error(
                "predict_markets.ligue1_fail",
                fixture_id=f["id"],
                match=f"{f['home_team_name']} vs {f['away_team_name']}",
                error=str(exc),
            )

    return n_predicted, len(fixtures)


# ─── NBA (baseline EWMA + Normal CDF) ─────────────────────────────────────────

def _predict_nba(horizon_hours: int) -> tuple[int, int]:
    matches = session.fetch_all(
        """
        SELECT f.id, f.home_team_id, f.away_team_id, f.match_date,
               f.home_score, f.away_score, f.competition
        FROM fixtures f
        WHERE f.sport = 'nba'
        ORDER BY f.match_date
        """
    )
    model: NBAScoringModel = fit_nba_scoring(matches)
    if not model.converged:
        log.warning("predict_markets.no_nba_model", n_matches=len(matches))
        return 0, 0

    log.info(
        "predict_markets.nba_fit",
        n_matches=len(matches),
        n_teams=len(model.off_rating),
        league_avg=round(model.league_avg, 1),
    )

    now = datetime.now(UTC)
    horizon = now + timedelta(hours=horizon_hours)
    fixtures = session.fetch_all(
        """
        SELECT f.id, f.home_team_id, f.away_team_id, f.home_team_name, f.away_team_name
        FROM fixtures f
        WHERE f.sport = 'nba'
          AND f.status IN ('SCHEDULED', 'TIMED')
          AND f.match_date BETWEEN %s AND %s
        """,
        (now, horizon),
    )

    n_predicted = 0
    for f in fixtures:
        home = f["home_team_id"]
        away = f["away_team_id"]
        try:
            # O/U : uniquement les lines effectivement offertes (NBA var très)
            for line in _available_lines(f["id"], "ou_total"):
                p_over, p_under = model.predict_over_under(home, away, line=line)
                _upsert(f["id"], "ou_total", "over",  line, p_over,  "nba_scoring")
                _upsert(f["id"], "ou_total", "under", line, p_under, "nba_scoring")
                n_predicted += 2

            # Spreads : chaque (selection, line) offerte
            for selection, line in _available_spread_selections(f["id"]):
                p_cover = model.predict_spread(home, away, selection, line)
                _upsert(f["id"], "spreads", selection, line, p_cover, "nba_scoring")
                n_predicted += 1
        except Exception as exc:
            log.error(
                "predict_markets.nba_fail",
                fixture_id=f["id"],
                match=f"{f['home_team_name']} vs {f['away_team_name']}",
                error=str(exc),
            )

    return n_predicted, len(fixtures)


def run_predict_markets(horizon_hours: int = _DEFAULT_HORIZON_HOURS) -> dict:
    """Prédit les markets dérivés pour Ligue 1 (DC) et NBA (baseline EWMA)."""
    l1_pred, l1_fx = _predict_ligue1(horizon_hours)
    nba_pred, nba_fx = _predict_nba(horizon_hours)
    total = l1_pred + nba_pred
    log.info(
        "predict_markets.done",
        ligue1_predicted=l1_pred,
        ligue1_fixtures=l1_fx,
        nba_predicted=nba_pred,
        nba_fixtures=nba_fx,
        total=total,
    )
    return {
        "predicted": total,
        "fixtures": l1_fx + nba_fx,
        "ligue1_predicted": l1_pred,
        "nba_predicted": nba_pred,
    }
