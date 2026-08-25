import {
  getAccuracyOverTime,
  getCalibrationData,
  getRecentPredictions,
} from "@/lib/db";
import AccuracyChart from "@/components/AccuracyChart";
import CalibrationChart from "@/components/CalibrationChart";

export const dynamic = "force-dynamic";

export default async function Ligue1Page() {
  const [accuracy, calibration, recent] = await Promise.all([
    getAccuracyOverTime("ligue1"),
    getCalibrationData("ligue1"),
    getRecentPredictions("ligue1", 25),
  ]);

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Ligue 1</h1>

      {/* ── Graphique accuracy / Brier ──────────────────────────────────────── */}
      <div className="card">
        <h2 className="text-base font-semibold mb-4">Accuracy & Brier score (par semaine)</h2>
        <AccuracyChart data={accuracy as any[]} />
      </div>

      {/* ── Courbe de calibration ─────────────────────────────────────────── */}
      <div className="card">
        <h2 className="text-base font-semibold mb-1">Courbe de calibration — victoire domicile</h2>
        <p className="text-xs text-muted mb-4">
          Chaque point = bucket de 10%. Sur la diagonale = parfaitement calibré.
        </p>
        <CalibrationChart data={calibration as any[]} />
      </div>

      {/* ── Prédictions récentes ──────────────────────────────────────────── */}
      <div className="card">
        <h2 className="text-base font-semibold mb-4">Prédictions récentes</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left py-2 pr-4">Match</th>
                <th className="text-left py-2 pr-4">Date</th>
                <th className="text-center py-2 pr-4">Prédit</th>
                <th className="text-center py-2 pr-4">P(Dom)</th>
                <th className="text-center py-2 pr-4">P(Nul)</th>
                <th className="text-center py-2 pr-4">P(Ext)</th>
                <th className="text-center py-2 pr-4">Réel</th>
                <th className="text-center py-2">Brier</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r: any, i: number) => (
                <tr key={i} className="border-b border-border/50 hover:bg-white/5">
                  <td className="py-2 pr-4 font-medium">
                    {r.homeTeamName} <span className="text-muted">vs</span> {r.awayTeamName}
                  </td>
                  <td className="py-2 pr-4 text-muted">
                    {new Date(r.matchDate).toLocaleDateString("fr-FR")}
                  </td>
                  <td className="py-2 pr-4 text-center">
                    <span className="badge-neutral">{r.predictedOutcome}</span>
                  </td>
                  <td className="py-2 pr-4 text-center text-muted">{r.probHome}</td>
                  <td className="py-2 pr-4 text-center text-muted">{r.probDraw ?? "—"}</td>
                  <td className="py-2 pr-4 text-center text-muted">{r.probAway}</td>
                  <td className="py-2 pr-4 text-center">
                    {r.actualOutcome ? (
                      <span className={r.isCorrect ? "badge-success" : "badge-danger"}>
                        {r.actualOutcome}
                      </span>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="py-2 text-center text-muted">
                    {r.brierScore ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
