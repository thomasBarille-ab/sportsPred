import {
  getAccuracyOverTime,
  getCalibrationData,
  getRecentPredictions,
  getFranceNTUpcoming,
  getFranceNTCompetitionStats,
} from "@/lib/db";
import AccuracyChart from "@/components/AccuracyChart";
import CalibrationChart from "@/components/CalibrationChart";
import ProbStack from "@/components/ProbStack";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

export const dynamic = "force-dynamic";

const OUTCOME_LABEL: Record<string, string> = {
  home: "Dom.",
  draw: "Nul",
  away: "Ext.",
};

const OUTCOME_STYLE: Record<string, string> = {
  home: "bg-blue-500/20 text-blue-300",
  draw: "bg-slate-500/20 text-slate-300",
  away: "bg-orange-500/20 text-orange-300",
};

const COMPETITION_LABEL: Record<string, string> = {
  FRIENDLY:  "Amical",
  UEFA_UNL:  "Nations League",
  FIFA_WCQ:  "Qualif. CM",
  UEFA_ECQ:  "Qualif. Euro",
  FIFA_WC:   "Coupe du Monde",
  UEFA_EC:   "Euro",
};

const COMPETITION_STYLE: Record<string, string> = {
  FRIENDLY:  "bg-slate-500/20 text-slate-400",
  UEFA_UNL:  "bg-indigo-500/20 text-indigo-300",
  FIFA_WCQ:  "bg-yellow-500/20 text-yellow-300",
  UEFA_ECQ:  "bg-yellow-500/20 text-yellow-300",
  FIFA_WC:   "bg-amber-500/20 text-amber-300",
  UEFA_EC:   "bg-emerald-500/20 text-emerald-300",
};

function CompBadge({ code }: { code: string }) {
  const label = COMPETITION_LABEL[code] ?? code;
  const style = COMPETITION_STYLE[code] ?? "bg-slate-500/20 text-slate-400";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${style}`}>
      {label}
    </span>
  );
}

export default async function FrancePage() {
  const [accuracy, calibration, recent, upcoming, compStats] = await Promise.all([
    getAccuracyOverTime("france_nt"),
    getCalibrationData("france_nt"),
    getRecentPredictions("france_nt", 30),
    getFranceNTUpcoming(8).catch(() => []),
    getFranceNTCompetitionStats().catch(() => []),
  ]);

  const now = new Date();

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">Équipe de France</h1>
        <span className="text-xs text-muted bg-blue-500/10 border border-blue-500/20 px-2 py-0.5 rounded">
          NT · france_nt
        </span>
      </div>

      {/* Prochains matchs */}
      {(upcoming as any[]).length > 0 && (
        <div className="card">
          <h2 className="text-base font-semibold mb-4">Prochains matchs</h2>
          <div className="space-y-3">
            {(upcoming as any[]).map((m, i) => {
              const franceIsHome = Number(m.franceIsHome ?? 1) === 1;
              const opponentName = franceIsHome ? m.awayTeamName : m.homeTeamName;
              const matchDate = new Date(m.matchDate);

              const probs = m.probHome != null
                ? [
                    { label: franceIsHome ? "France" : m.homeTeamName, value: Number(m.probHome) },
                    { label: "Nul", value: Number(m.probDraw ?? 0) },
                    { label: franceIsHome ? opponentName : "France", value: Number(m.probAway) },
                  ]
                : null;

              return (
                <div key={i} className="flex items-center gap-4 py-3 border-b border-border/40 last:border-0">
                  <div className="w-28 shrink-0">
                    <CompBadge code={m.competition ?? "FRIENDLY"} />
                  </div>
                  <div className="flex-1 font-medium">
                    <span className="text-blue-300">France</span>
                    <span className="text-muted mx-2 text-xs">{franceIsHome ? "vs" : "@"}</span>
                    <span>{opponentName}</span>
                  </div>
                  <div className="text-xs text-muted whitespace-nowrap">
                    {format(matchDate, "dd MMM HH:mm", { locale: fr })}
                  </div>
                  {probs ? (
                    <div className="w-48">
                      <ProbStack probs={probs} />
                    </div>
                  ) : (
                    <span className="text-xs text-muted w-48">Pas encore prédit</span>
                  )}
                  {m.predictedOutcome && (
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                        OUTCOME_STYLE[m.predictedOutcome] ?? ""
                      }`}
                    >
                      {OUTCOME_LABEL[m.predictedOutcome]}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Accuracy & Brier */}
      {(accuracy as any[]).length > 0 ? (
        <>
          <div className="card">
            <h2 className="text-base font-semibold mb-4">Accuracy &amp; Brier score (par semaine)</h2>
            <AccuracyChart data={accuracy as any[]} />
          </div>
          <p className="text-xs text-muted -mt-4">
            Brier score France NT : 3 classes (victoire France / nul / défaite France).
            Baseline modèle aléatoire = <strong>0.667</strong>.
          </p>
        </>
      ) : (
        <div className="card text-muted text-sm">
          Pas encore de prédictions évaluées — le modèle se construit après le backfill et le premier retrain.
        </div>
      )}

      {(accuracy as any[]).length > 0 && (
        <div className="card">
          <h2 className="text-base font-semibold mb-1">Courbe de calibration — victoire domicile</h2>
          <p className="text-xs text-muted mb-4">Sur la diagonale = parfaitement calibré.</p>
          <CalibrationChart data={calibration as any[]} />
        </div>
      )}

      {/* Stats par compétition */}
      {(compStats as any[]).length > 0 && (
        <div className="card">
          <h2 className="text-base font-semibold mb-4">Performance par compétition</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left py-2 pr-6">Compétition</th>
                <th className="text-center py-2 pr-6">Matchs</th>
                <th className="text-center py-2 pr-6">Accuracy</th>
                <th className="text-right py-2">Brier moy.</th>
              </tr>
            </thead>
            <tbody>
              {(compStats as any[]).map((row, i) => {
                const acc = Number(row.accuracy);
                const accuracyStyle =
                  acc >= 0.6 ? "text-emerald-400 font-semibold"
                  : acc >= 0.4 ? "text-yellow-400"
                  : "text-red-400";
                return (
                  <tr key={i} className="border-b border-border/40 hover:bg-white/5">
                    <td className="py-2.5 pr-6">
                      <CompBadge code={row.competition} />
                    </td>
                    <td className="py-2.5 pr-6 text-center text-muted">{row.nScored}</td>
                    <td className={`py-2.5 pr-6 text-center ${accuracyStyle}`}>
                      {Math.round(acc * 100)} %
                    </td>
                    <td className="py-2.5 text-right text-muted text-xs font-mono tabular-nums">
                      {Number(row.avgBrier).toFixed(4)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Prédictions récentes */}
      <div className="card">
        <h2 className="text-base font-semibold mb-4">Historique des prédictions</h2>
        {(recent as any[]).length === 0 ? (
          <p className="text-muted text-sm">Aucune prédiction pour l&apos;instant.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="text-left py-2 pr-6">Match</th>
                  <th className="text-left py-2 pr-6">Date</th>
                  <th className="text-center py-2 pr-6">Prédit</th>
                  <th className="text-left py-2 pr-6">Probabilités</th>
                  <th className="text-center py-2 pr-4">Résultat</th>
                  <th className="text-right py-2 pr-4">Brier</th>
                  <th className="text-left py-2">Analyse</th>
                </tr>
              </thead>
              <tbody>
                {(recent as any[]).map((r, i) => {
                  const matchDate = new Date(r.matchDate);
                  const isPast = matchDate < now;
                  const rowBg =
                    r.isCorrect === true ? "bg-emerald-500/5"
                    : r.isCorrect === false ? "bg-red-500/5"
                    : "";
                  const probs = [
                    { label: "Dom", value: Number(r.probHome) },
                    { label: "Nul", value: Number(r.probDraw ?? 0) },
                    { label: "Ext", value: Number(r.probAway) },
                  ];
                  return (
                    <tr
                      key={i}
                      className={`border-b border-border/40 hover:bg-white/5 transition-colors ${rowBg}`}
                    >
                      <td className="py-2.5 pr-6 font-medium whitespace-nowrap">
                        {r.homeTeamName}{" "}
                        <span className="text-muted text-xs">vs</span>{" "}
                        {r.awayTeamName}
                      </td>
                      <td className="py-2.5 pr-6 text-muted text-xs whitespace-nowrap">
                        {isPast
                          ? format(matchDate, "dd MMM yyyy", { locale: fr })
                          : <span className="text-blue-400">{format(matchDate, "dd MMM HH:mm", { locale: fr })}</span>
                        }
                      </td>
                      <td className="py-2.5 pr-6 text-center">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${OUTCOME_STYLE[r.predictedOutcome] ?? ""}`}>
                          {OUTCOME_LABEL[r.predictedOutcome] ?? r.predictedOutcome}
                        </span>
                      </td>
                      <td className="py-2.5 pr-6">
                        <ProbStack probs={probs} />
                      </td>
                      <td className="py-2.5 pr-4 text-center">
                        {r.actualOutcome ? (
                          <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${r.isCorrect ? "bg-emerald-500/20 text-emerald-400" : "bg-red-500/20 text-red-400"}`}>
                            {r.isCorrect ? "✓ " : "✗ "}
                            {OUTCOME_LABEL[r.actualOutcome] ?? r.actualOutcome}
                          </span>
                        ) : (
                          <span className="text-muted text-xs">À jouer</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-4 text-right text-muted text-xs font-mono tabular-nums">
                        {r.brierScore != null ? Number(r.brierScore).toFixed(4) : "—"}
                      </td>
                      <td className="py-2.5 text-xs text-slate-400 max-w-xs">
                        {r.explanation ?? <span className="text-muted/40">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
