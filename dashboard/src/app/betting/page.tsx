import { getBettingKPIs, getBankrollHistory, getUpcomingValueBets, getBetHistory } from "@/lib/db";
import { BankrollChart } from "@/components/BankrollChart";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

const OUTCOME_LABEL: Record<string, string> = {
  home: "Domicile",
  draw: "Nul",
  away: "Extérieur",
};

const SPORT_LABEL: Record<string, string> = {
  ligue1: "L1",
  nba: "NBA",
};

export default async function BettingPage() {
  const [kpis, history, upcoming, history_bets] = await Promise.all([
    getBettingKPIs().catch(() => null),
    getBankrollHistory().catch(() => []),
    getUpcomingValueBets().catch(() => []),
    getBetHistory(50).catch(() => []),
  ]);

  const chartData = (history as { day: Date; cumulativePnl: number }[]).map((r) => ({
    day: r.day instanceof Date ? r.day.toISOString() : String(r.day),
    cumulativePnl: Number(r.cumulativePnl),
  }));

  const totalPnl = Number(kpis?.totalPnl ?? 0);
  const winRate = Number(kpis?.winRate ?? 0);
  const avgEv = Number(kpis?.avgEvPct ?? 0);

  return (
    <div className="p-8 space-y-8">
      <h1 className="text-2xl font-bold text-white">Simulation de paris</h1>

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-card border border-border rounded-xl p-5">
          <p className="text-xs text-muted mb-1">Paris simulés</p>
          <p className="text-3xl font-bold text-white">{Number(kpis?.totalBets ?? 0)}</p>
          <p className="text-xs text-muted mt-1">
            {Number(kpis?.won ?? 0)}W / {Number(kpis?.lost ?? 0)}L / {Number(kpis?.pending ?? 0)} en cours
          </p>
        </div>

        <div className="bg-card border border-border rounded-xl p-5">
          <p className="text-xs text-muted mb-1">Win rate</p>
          <p className={`text-3xl font-bold ${winRate >= 0.5 ? "text-green-400" : "text-red-400"}`}>
            {kpis?.won != null ? `${(winRate * 100).toFixed(1)}%` : "—"}
          </p>
          <p className="text-xs text-muted mt-1">sur paris réglés</p>
        </div>

        <div className="bg-card border border-border rounded-xl p-5">
          <p className="text-xs text-muted mb-1">P&L total</p>
          <p className={`text-3xl font-bold ${totalPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
            {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)} u
          </p>
          <p className="text-xs text-muted mt-1">unités (mise = 1u)</p>
        </div>

        <div className="bg-card border border-border rounded-xl p-5">
          <p className="text-xs text-muted mb-1">EV moyen</p>
          <p className={`text-3xl font-bold ${avgEv >= 0 ? "text-green-400" : "text-red-400"}`}>
            {kpis?.avgEvPct != null ? `${(avgEv * 100).toFixed(1)}%` : "—"}
          </p>
          <p className="text-xs text-muted mt-1">par pari simulé</p>
        </div>
      </div>

      {/* Bankroll chart */}
      <div className="bg-card border border-border rounded-xl p-6">
        <h2 className="text-sm font-semibold text-white mb-4">P&L cumulé (unités)</h2>
        <BankrollChart data={chartData} />
      </div>

      {/* Upcoming value bets */}
      {(upcoming as unknown[]).length > 0 && (
        <div className="bg-card border border-border rounded-xl p-6">
          <h2 className="text-sm font-semibold text-white mb-4">
            Paris à venir ({(upcoming as unknown[]).length})
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-xs uppercase tracking-wide border-b border-border">
                  <th className="text-left py-2 pr-4">Match</th>
                  <th className="text-left py-2 pr-4">Sport</th>
                  <th className="text-left py-2 pr-4">Pari</th>
                  <th className="text-left py-2 pr-4">Cote</th>
                  <th className="text-left py-2 pr-4">EV</th>
                  <th className="text-left py-2">Bookmaker</th>
                </tr>
              </thead>
              <tbody>
                {(upcoming as {
                  homeTeamName: string;
                  awayTeamName: string;
                  matchDate: Date;
                  sport: string;
                  betOutcome: string;
                  bookmaker: string;
                  oddsTaken: number;
                  evPct: number;
                }[]).map((b, i) => (
                  <tr key={i} className="border-b border-border/40 hover:bg-border/20">
                    <td className="py-2 pr-4 text-white font-medium">
                      {b.homeTeamName} <span className="text-muted">vs</span> {b.awayTeamName}
                      <span className="block text-xs text-muted">
                        {b.matchDate instanceof Date
                          ? format(b.matchDate, "dd MMM HH:mm", { locale: fr })
                          : String(b.matchDate)}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-muted">{SPORT_LABEL[b.sport] ?? b.sport}</td>
                    <td className="py-2 pr-4">
                      <span className="px-2 py-0.5 bg-blue-500/20 text-blue-300 rounded text-xs font-medium">
                        {OUTCOME_LABEL[b.betOutcome] ?? b.betOutcome}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-white">{Number(b.oddsTaken).toFixed(2)}</td>
                    <td className="py-2 pr-4">
                      <span className="text-green-400 font-medium">
                        +{(Number(b.evPct) * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td className="py-2 text-muted capitalize">{b.bookmaker}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Bet history */}
      <div className="bg-card border border-border rounded-xl p-6">
        <h2 className="text-sm font-semibold text-white mb-4">Historique des paris</h2>
        {(history_bets as unknown[]).length === 0 ? (
          <p className="text-muted text-sm">Aucun pari réglé pour l&apos;instant.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-xs uppercase tracking-wide border-b border-border">
                  <th className="text-left py-2 pr-4">Match</th>
                  <th className="text-left py-2 pr-4">Sport</th>
                  <th className="text-left py-2 pr-4">Pari</th>
                  <th className="text-left py-2 pr-4">Cote</th>
                  <th className="text-left py-2 pr-4">EV</th>
                  <th className="text-left py-2 pr-4">Résultat</th>
                  <th className="text-left py-2">P&L</th>
                </tr>
              </thead>
              <tbody>
                {(history_bets as {
                  homeTeamName: string;
                  awayTeamName: string;
                  matchDate: Date;
                  sport: string;
                  betOutcome: string;
                  oddsTaken: number;
                  evPct: number;
                  status: string;
                  pnlUnits: number;
                  settledAt: Date;
                }[]).map((b, i) => (
                  <tr key={i} className="border-b border-border/40 hover:bg-border/20">
                    <td className="py-2 pr-4 text-white">
                      {b.homeTeamName} <span className="text-muted">vs</span> {b.awayTeamName}
                    </td>
                    <td className="py-2 pr-4 text-muted">{SPORT_LABEL[b.sport] ?? b.sport}</td>
                    <td className="py-2 pr-4 text-muted">{OUTCOME_LABEL[b.betOutcome] ?? b.betOutcome}</td>
                    <td className="py-2 pr-4 text-white">{Number(b.oddsTaken).toFixed(2)}</td>
                    <td className="py-2 pr-4 text-muted">{(Number(b.evPct) * 100).toFixed(1)}%</td>
                    <td className="py-2 pr-4">
                      {b.status === "won" ? (
                        <span className="text-green-400 font-medium">Gagné</span>
                      ) : (
                        <span className="text-red-400 font-medium">Perdu</span>
                      )}
                    </td>
                    <td className={`py-2 font-medium ${Number(b.pnlUnits) >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {Number(b.pnlUnits) >= 0 ? "+" : ""}{Number(b.pnlUnits).toFixed(2)} u
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
