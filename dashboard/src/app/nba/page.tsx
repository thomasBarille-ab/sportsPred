import {
  getAccuracyOverTime,
  getCalibrationData,
  getRecentPredictions,
} from "@/lib/db";
import AccuracyChart from "@/components/AccuracyChart";
import CalibrationChart from "@/components/CalibrationChart";
import ProbStack from "@/components/ProbStack";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

export const dynamic = "force-dynamic";

const OUTCOME_LABEL: Record<string, string> = {
  home: "Dom.",
  away: "Ext.",
};

const OUTCOME_STYLE: Record<string, string> = {
  home: "bg-blue-500/20 text-blue-300",
  away: "bg-orange-500/20 text-orange-300",
};

export default async function NbaPage() {
  const [accuracy, calibration, recent] = await Promise.all([
    getAccuracyOverTime("nba"),
    getCalibrationData("nba"),
    getRecentPredictions("nba", 25),
  ]);

  const now = new Date();

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">NBA</h1>

      <div className="card">
        <h2 className="text-base font-semibold mb-4">Accuracy &amp; Brier score (par semaine)</h2>
        <AccuracyChart data={accuracy as any[]} />
      </div>

      <div className="card">
        <h2 className="text-base font-semibold mb-1">Courbe de calibration — victoire domicile</h2>
        <p className="text-xs text-muted mb-4">
          Baseline aléatoire NBA : ~0.25 Brier (2 issues). Sur la diagonale = parfaitement calibré.
        </p>
        <CalibrationChart data={calibration as any[]} />
      </div>

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
                <th className="text-right py-2 pr-4">Brier</th>
                <th className="text-left py-2">Analyse</th>
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
      </div>
    </div>
  );
}
