import {
  getAccuracyOverTime,
  getCalibrationData,
  getRecentPredictions,
  getMatchdaySummary,
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

export default async function Ligue1Page() {
  const [accuracy, calibration, recent, matchdays] = await Promise.all([
    getAccuracyOverTime("ligue1"),
    getCalibrationData("ligue1"),
    getRecentPredictions("ligue1", 25),
    getMatchdaySummary("ligue1"),
  ]);

  const now = new Date();

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Ligue 1</h1>

      <div className="card">
        <h2 className="text-base font-semibold mb-4">Accuracy &amp; Brier score (par semaine)</h2>
        <AccuracyChart data={accuracy as any[]} />
      </div>

      <p className="text-xs text-muted -mt-4">
        Brier score Ligue 1 : somme des carrés sur 3 classes (home/nul/away).
        Baseline modèle aléatoire = <strong>0.667</strong>. Meilleur = plus proche de 0.
      </p>

      <div className="card">
        <h2 className="text-base font-semibold mb-1">Courbe de calibration — victoire domicile</h2>
        <p className="text-xs text-muted mb-4">
          Chaque point = bucket de 10 %. Sur la diagonale = parfaitement calibré.
        </p>
        <CalibrationChart data={calibration as any[]} />
      </div>

      {(matchdays as any[]).length > 0 && (
        <div className="card">
          <h2 className="text-base font-semibold mb-4">Résumé par journée</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted border-b border-border">
                  <th className="text-left py-2 pr-6">Journée</th>
                  <th className="text-left py-2 pr-6">Période</th>
                  <th className="text-center py-2 pr-6">Prédits</th>
                  <th className="text-center py-2 pr-6">Corrects</th>
                  <th className="text-center py-2 pr-6">Accuracy</th>
                  <th className="text-right py-2">Brier moy.</th>
                </tr>
              </thead>
              <tbody>
                {(matchdays as any[]).map((row, i) => {
                  const accuracy = row.accuracy != null ? Number(row.accuracy) : null;
                  const correct = Number(row.correct ?? 0);
                  const scored = Number(row.scored ?? 0);
                  const total = Number(row.total ?? 0);
                  const firstDate = row.firstMatch ? new Date(row.firstMatch) : null;
                  const lastDate = row.lastMatch ? new Date(row.lastMatch) : null;

                  const accuracyStyle =
                    accuracy == null
                      ? "text-muted"
                      : accuracy >= 0.6
                      ? "text-emerald-400 font-semibold"
                      : accuracy >= 0.4
                      ? "text-yellow-400"
                      : "text-red-400";

                  const period =
                    firstDate && lastDate
                      ? firstDate.toLocaleDateString("fr-FR", { day: "2-digit", month: "short" }) +
                        (firstDate.getTime() !== lastDate.getTime()
                          ? " – " + lastDate.toLocaleDateString("fr-FR", { day: "2-digit", month: "short" })
                          : "")
                      : "—";

                  return (
                    <tr
                      key={i}
                      className="border-b border-border/40 hover:bg-white/5 transition-colors"
                    >
                      <td className="py-2.5 pr-6 font-semibold">J{row.matchday}</td>
                      <td className="py-2.5 pr-6 text-muted text-xs whitespace-nowrap">{period}</td>
                      <td className="py-2.5 pr-6 text-center text-muted">{total}</td>
                      <td className="py-2.5 pr-6 text-center">
                        {scored > 0 ? (
                          <span>
                            {correct}{" "}
                            <span className="text-muted text-xs">/ {scored}</span>
                          </span>
                        ) : (
                          <span className="text-muted text-xs">—</span>
                        )}
                      </td>
                      <td className={`py-2.5 pr-6 text-center ${accuracyStyle}`}>
                        {accuracy != null ? `${Math.round(accuracy * 100)} %` : "—"}
                      </td>
                      <td className="py-2.5 text-right text-muted text-xs font-mono tabular-nums">
                        {row.avgBrier != null ? Number(row.avgBrier).toFixed(4) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <h2 className="text-base font-semibold mb-4">Prédictions récentes</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left py-2 pr-6">Match</th>
                <th className="text-left py-2 pr-6">Date</th>
                <th className="text-center py-2 pr-6">Prédit</th>
                <th className="text-left py-2 pr-6">Probabilités</th>
                <th className="text-center py-2 pr-4">Résultat</th>
                <th className="text-right py-2">Brier</th>
              </tr>
            </thead>
            <tbody>
              {(recent as any[]).map((r, i) => {
                const matchDate = new Date(r.matchDate);
                const isPast = matchDate < now;
                const rowBg =
                  r.isCorrect === true
                    ? "bg-emerald-500/5"
                    : r.isCorrect === false
                    ? "bg-red-500/5"
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
                      {isPast ? (
                        format(matchDate, "dd MMM yyyy", { locale: fr })
                      ) : (
                        <span className="text-blue-400">
                          {format(matchDate, "dd MMM HH:mm", { locale: fr })}
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-6 text-center">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                          OUTCOME_STYLE[r.predictedOutcome] ?? "bg-slate-600/40 text-slate-300"
                        }`}
                      >
                        {OUTCOME_LABEL[r.predictedOutcome] ?? r.predictedOutcome}
                      </span>
                    </td>
                    <td className="py-2.5 pr-6">
                      <ProbStack probs={probs} />
                    </td>
                    <td className="py-2.5 pr-4 text-center">
                      {r.actualOutcome ? (
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                            r.isCorrect
                              ? "bg-emerald-500/20 text-emerald-400"
                              : "bg-red-500/20 text-red-400"
                          }`}
                        >
                          {r.isCorrect ? "✓ " : "✗ "}
                          {OUTCOME_LABEL[r.actualOutcome] ?? r.actualOutcome}
                        </span>
                      ) : (
                        <span className="text-muted text-xs">À jouer</span>
                      )}
                    </td>
                    <td className="py-2.5 text-right text-muted text-xs font-mono tabular-nums">
                      {r.brierScore != null ? Number(r.brierScore).toFixed(4) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
