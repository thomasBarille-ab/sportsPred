"""Prédictions multi-markets (O/U 2.5, BTTS, Double Chance) pour Ligue 1.

Dérive les probas depuis la matrice de scores Dixon-Coles du modèle en prod.
Les paris Double Chance ne sont pas simulés tant qu'une source d'odds DC
n'est pas branchée — mais la proba est stockée pour affichage éventuel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import joblib
import structlog

from ..db import session

log = structlog.get_logger()

_OU_LINE = 2.5
_DEFAULT_HORIZON_HOURS = 48


def _upsert(
    fixture_id: int,
    market: str,
    selection: str,
    line: float,
    prob: float,
) -> None:
    session.execute(
        """
        INSERT INTO predictions_markets
          (fixture_id, market, selection, line, prob, model_source)
        VALUES (%s, %s, %s, %s, %s, 'dixon_coles')
        ON CONFLICT (fixture_id, market, selection, line) DO UPDATE
          SET prob = EXCLUDED.prob, created_at = NOW()
        """,
        (fixture_id, market, selection, line, prob),
    )


def run_predict_markets(horizon_hours: int = _DEFAULT_HORIZON_HOURS) -> dict:
    """Calcule et stocke les probas O/U, BTTS, DC pour les prochains matchs ligue1."""
    prod_model = session.fetch_one(
        "SELECT * FROM model_versions WHERE sport = 'ligue1' AND is_production = TRUE LIMIT 1",
    )
    if not prod_model:
        log.warning("predict_markets.no_model", sport="ligue1")
        return {"predicted": 0, "fixtures": 0}

    artifact = joblib.load(prod_model["model_path"])
    dc_model = artifact.get("dc_model")
    if not dc_model or not getattr(dc_model, "converged", False):
        log.warning("predict_markets.no_dc_model", sport="ligue1")
        return {"predicted": 0, "fixtures": 0}

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
        home_id = f["home_team_id"]
        away_id = f["away_team_id"]

        try:
            p_over, p_under = dc_model.predict_over_under(home_id, away_id, line=_OU_LINE)
            p_btts_yes, p_btts_no = dc_model.predict_btts(home_id, away_id)
            p_1x, p_12, p_x2 = dc_model.predict_double_chance(home_id, away_id)
        except Exception as exc:
            log.error(
                "predict_markets.compute_failed",
                fixture_id=f["id"],
                match=f"{f['home_team_name']} vs {f['away_team_name']}",
                error=str(exc),
            )
            continue

        rows = [
            ("ou_total",      "over",  _OU_LINE, p_over),
            ("ou_total",      "under", _OU_LINE, p_under),
            ("btts",          "yes",   0.0,      p_btts_yes),
            ("btts",          "no",    0.0,      p_btts_no),
            ("double_chance", "1x",    0.0,      p_1x),
            ("double_chance", "12",    0.0,      p_12),
            ("double_chance", "x2",    0.0,      p_x2),
        ]
        for market, selection, line, prob in rows:
            _upsert(f["id"], market, selection, float(line), float(prob))
            n_predicted += 1

    log.info("predict_markets.done", fixtures=len(fixtures), predicted=n_predicted)
    return {"predicted": n_predicted, "fixtures": len(fixtures)}
