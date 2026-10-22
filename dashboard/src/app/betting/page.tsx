import { getBettingKPIs, getBankrollHistory, getUpcomingValueBets, getBetHistory } from "@/lib/db";
import { BankrollChart } from "@/components/BankrollChart";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

export const dynamic = "force-dynamic";

const SPORT_LABEL: Record<string, string> = {
  ligue1: "L1",
  nba: "NBA",
};

const MARKET_SHORT: Record<string, string> = {
  h2h: "1X2",
  ou_total: "O/U",
  btts: "BTTS",
  double_chance: "DC",
  spreads: "Spread",
};

function marketShort(market: string): string {
  return MARKET_SHORT[market] ?? market.toUpperCase();
}

function fmtSignedLine(line: number): string {
  if (line === 0) return "0";
  return line > 0 ? `+${line}` : `${line}`;
}

function selectionLabel(market: string, selection: string | null, line: number): string {
  if (!selection) return "?";
  if (market === "h2h") {
    return selection === "home" ? "1 Domicile"
         : selection === "draw" ? "X Nul"
         : selection === "away" ? "2 Extérieur"
         : selection;
  }
  if (market === "ou_total") {
    const dir = selection === "over" ? "Over" : selection === "under" ? "Under" : selection;
    return `${dir} ${line}`;
  }
  if (market === "btts") {
    return selection === "yes" ? "BTTS Oui" : selection === "no" ? "BTTS Non" : selection;
  }
  if (market === "double_chance") {
    const map: Record<string, string> = { "1x": "1X", "12": "12", "x2": "X2" };
    return `DC ${map[selection] ?? selection.toUpperCase()}`;
  }
  if (market === "spreads") {
    const side = selection === "home" ? "Home" : selection === "away" ? "Away" : selection;
    return `${side} ${fmtSignedLine(line)}`;
  }
  return selection;
}

type UpcomingBet = {
  id: number;
  fixtureId: number;
  homeTeamName: string;
  awayTeamName: string;
  matchDate: Date;
  sport: string;
  market: string;
  selection: string | null;
  line: number;
  betOutcome: string | null;
  bookmaker: string;
  oddsTaken: number;
  evPct: number;
};

type HistoryBet = UpcomingBet & {
  status: string;
  pnlUnits: number;
  settledAt: Date;
};

function groupByFixture<T extends { fixtureId: number }>(bets: T[]): T[][] {
  const map = new Map<number, T[]>();
  for (const b of bets) {
    const arr = map.get(b.fixtureId) ?? [];
    arr.push(b);
    map.set(b.fixtureId, arr);
  }
  return Array.from(map.values());
}

function fmtDate(d: Date | string): string {
  return d instanceof Date ? format(d, "dd MMM HH:mm", { locale: fr }) : String(d);
}

export default async function BettingPage() {
  const [kpis, history, upcomingRaw, historyRaw] = await Promise.all([
    getBettingKPIs().catch((e: unknown) => {
      console.error("[BettingPage] getBettingKPIs failed:", e instanceof Error ? e.message : String(e));
      return null;
    }),
    getBankrollHistory().catch(() => []),
    getUpcomingValueBets().catch(() => []),
    getBetHistory(100).catch(() => []),
  ]);

  const chartData = (history as { day: Date; cumulativePnl: number }[]).map((r) => ({
    day: r.day instanceof Date ? r.day.toISOString() : String(r.day),
    cumulativePnl: Number(r.cumulativePnl),
  }));

  const upcoming = upcomingRaw as UpcomingBet[];
  const historyBets = historyRaw as HistoryBet[];

  const upcomingGroups = groupByFixture(upcoming);
  const historyGroups = groupByFixture(historyBets);

  const totalBets = Number(kpis?.totalBets ?? 0);
  const won = Number(kpis?.won ?? 0);
  const lost = Number(kpis?.lost ?? 0);
  const pending = Number(kpis?.pending ?? 0);
  const resolved = won + lost;
  const totalPnl = Number(kpis?.totalPnl ?? 0);
  const winRate = resolved > 0 ? won / resolved : null;
  const avgEv = kpis?.avgEvPct != null ? Number(kpis.avgEvPct) : null;

  return (
    <div className="p-8 space-y-8">
      <h1 className="text-2xl font-bold text-white">Simulation de paris</h1>

      {/* KPI cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-card border border-border rounded-xl p-5">
          <p className="text-xs text-muted mb-1">Paris simulés</p>
          <p className="text-3xl font-bold text-white">{totalBets}</p>
          <p className="text-xs text-muted mt-1">
            {won}W / {lost}L / {pending} en cours
          </p>
        </div>

        <div className="bg-card border border-border rounded-xl p-5">
          <p className="text-xs text-muted mb-1">Win rate</p>
          <p className={`text-3xl font-bold ${winRate != null && winRate >= 0.5 ? "text-green-400" : "text-red-400"}`}>
            {winRate != null ? `${(winRate * 100).toFixed(1)}%` : "—"}
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
          <p className={`text-3xl font-bold ${avgEv != null && avgEv >= 0 ? "text-green-400" : "text-red-400"}`}>
            {avgEv != null ? `${(avgEv * 100).toFixed(1)}%` : "—"}
          </p>
          <p className="text-xs text-muted mt-1">par pari simulé</p>
        </div>
      </div>

      {/* Bankroll chart */}
      <div className="bg-card border border-border rounded-xl p-6">
        <h2 className="text-sm font-semibold text-white mb-4">P&L cumulé (unités)</h2>
        <BankrollChart data={chartData} />
      </div>

      {/* Upcoming — flat table, one row per bet, grouped by match via <details> */}
      {upcoming.length > 0 && (
        <div className="bg-card border border-border rounded-xl p-6">
          <h2 className="text-sm font-semibold text-white mb-1">
            Paris à venir ({upcoming.length} paris sur {upcomingGroups.length} match{upcomingGroups.length > 1 ? "s" : ""})
          </h2>
          <p className="text-xs text-slate-500 mb-4">
            1X2 = victoire dom./nul/ext. · O/U = Over/Under total buts · BTTS = les 2 équipes marquent · DC = Double Chance
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-xs uppercase tracking-wide border-b border-border">
                  <th className="text-left py-2 pr-4">Match</th>
                  <th className="text-left py-2 pr-4">Sport</th>
                  <th className="text-left py-2 pr-4">Market</th>
                  <th className="text-left py-2 pr-4">Pari</th>
                  <th className="text-left py-2 pr-4">Cote</th>
                  <th className="text-left py-2 pr-4">EV <span className="font-normal text-slate-500">(espérance de gain)</span></th>
                  <th className="text-left py-2">Bookmaker</th>
                </tr>
              </thead>
              <tbody>
                {upcoming.map((b) => (
                  <tr key={b.id} className="border-b border-border/40 hover:bg-border/20">
                    <td className="py-2 pr-4 text-white font-medium">
                      {b.homeTeamName} <span className="text-muted">vs</span> {b.awayTeamName}
                      <span className="block text-xs text-muted">{fmtDate(b.matchDate)}</span>
                    </td>
                    <td className="py-2 pr-4 text-muted">{SPORT_LABEL[b.sport] ?? b.sport}</td>
                    <td className="py-2 pr-4">
                      <span className="px-2 py-0.5 bg-slate-500/20 text-slate-300 rounded text-xs font-medium">
                        {marketShort(b.market)}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      <span className="px-2 py-0.5 bg-blue-500/20 text-blue-300 rounded text-xs font-medium">
                        {selectionLabel(b.market, b.selection, b.line)}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-white">{Number(b.oddsTaken).toFixed(2)}</td>
                    <td className="py-2 pr-4">
                      <span className={`font-medium ${Number(b.evPct) >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {Number(b.evPct) >= 0 ? "+" : ""}{(Number(b.evPct) * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td className="py-2 text-muted capitalize">{b.bookmaker}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Vue groupée par match (accordion) */}
          {upcomingGroups.some((g) => g.length > 1) && (
            <div className="mt-6">
              <h3 className="text-xs text-muted uppercase tracking-wide mb-2">Voir par match</h3>
              <div className="space-y-2">
                {upcomingGroups
                  .filter((g) => g.length > 1)
                  .map((group) => (
                    <details key={group[0].fixtureId} className="bg-background/40 rounded-lg border border-border/40">
                      <summary className="cursor-pointer p-3 flex items-center gap-3 hover:bg-border/10 rounded-lg">
                        <span className="text-white font-medium">
                          {group[0].homeTeamName} <span className="text-muted">vs</span> {group[0].awayTeamName}
                        </span>
                        <span className="text-xs text-muted">{fmtDate(group[0].matchDate)}</span>
                        <span className="ml-auto text-xs px-2 py-0.5 bg-blue-500/20 text-blue-300 rounded font-medium">
                          {group.length} paris
                        </span>
                      </summary>
                      <div className="px-3 pb-3 pt-1 space-y-1">
                        {group.map((b) => (
                          <div key={b.id} className="flex items-center gap-3 text-sm py-1">
                            <span className="px-2 py-0.5 bg-slate-500/20 text-slate-300 rounded text-xs">
                              {marketShort(b.market)}
                            </span>
                            <span className="text-blue-300">{selectionLabel(b.market, b.selection, b.line)}</span>
                            <span className="text-muted">@ {Number(b.oddsTaken).toFixed(2)}</span>
                            <span className={`ml-auto font-medium ${Number(b.evPct) >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {Number(b.evPct) >= 0 ? "+" : ""}{(Number(b.evPct) * 100).toFixed(1)}% EV
                            </span>
                            <span className="text-xs text-muted capitalize">{b.bookmaker}</span>
                          </div>
                        ))}
                      </div>
                    </details>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Bet history — flat table, one row per bet, with Market column */}
      <div className="bg-card border border-border rounded-xl p-6">
        <h2 className="text-sm font-semibold text-white mb-4">
          Historique des paris
          {historyBets.length > 0 && (
            <span className="text-muted font-normal"> — {historyBets.length} paris sur {historyGroups.length} match{historyGroups.length > 1 ? "s" : ""}</span>
          )}
        </h2>
        {historyBets.length === 0 ? (
          <p className="text-muted text-sm">Aucun pari réglé pour l&apos;instant.</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted text-xs uppercase tracking-wide border-b border-border">
                    <th className="text-left py-2 pr-4">Match</th>
                    <th className="text-left py-2 pr-4">Sport</th>
                    <th className="text-left py-2 pr-4">Market</th>
                    <th className="text-left py-2 pr-4">Pari</th>
                    <th className="text-left py-2 pr-4">Cote</th>
                    <th className="text-left py-2 pr-4">EV <span className="font-normal text-slate-500 normal-case">(espérance)</span></th>
                    <th className="text-left py-2 pr-4">Résultat</th>
                    <th className="text-left py-2">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {historyBets.map((b) => (
                    <tr key={b.id} className="border-b border-border/40 hover:bg-border/20">
                      <td className="py-2 pr-4 text-white">
                        {b.homeTeamName} <span className="text-muted">vs</span> {b.awayTeamName}
                      </td>
                      <td className="py-2 pr-4 text-muted">{SPORT_LABEL[b.sport] ?? b.sport}</td>
                      <td className="py-2 pr-4">
                        <span className="px-2 py-0.5 bg-slate-500/20 text-slate-300 rounded text-xs font-medium">
                          {marketShort(b.market)}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-muted">{selectionLabel(b.market, b.selection, b.line)}</td>
                      <td className="py-2 pr-4 text-white">{Number(b.oddsTaken).toFixed(2)}</td>
                      <td className="py-2 pr-4 text-muted">{(Number(b.evPct) * 100).toFixed(1)}%</td>
                      <td className="py-2 pr-4">
                        {b.status === "won" ? (
                          <span className="text-green-400 font-medium">Gagné</span>
                        ) : b.status === "lost" ? (
                          <span className="text-red-400 font-medium">Perdu</span>
                        ) : (
                          <span className="text-muted">—</span>
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

            {/* Vue groupée par match (accordion) pour les matchs à N paris */}
            {historyGroups.some((g) => g.length > 1) && (
              <div className="mt-6">
                <h3 className="text-xs text-muted uppercase tracking-wide mb-2">Voir par match</h3>
                <div className="space-y-2">
                  {historyGroups
                    .filter((g) => g.length > 1)
                    .map((group) => {
                      const groupPnl = group.reduce((sum, b) => sum + Number(b.pnlUnits ?? 0), 0);
                      return (
                        <details key={group[0].fixtureId} className="bg-background/40 rounded-lg border border-border/40">
                          <summary className="cursor-pointer p-3 flex items-center gap-3 hover:bg-border/10 rounded-lg">
                            <span className="text-white font-medium">
                              {group[0].homeTeamName} <span className="text-muted">vs</span> {group[0].awayTeamName}
                            </span>
                            <span className="text-xs text-muted">{fmtDate(group[0].matchDate)}</span>
                            <span className="ml-auto text-xs px-2 py-0.5 bg-blue-500/20 text-blue-300 rounded font-medium">
                              {group.length} paris
                            </span>
                            <span className={`text-sm font-medium ${groupPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {groupPnl >= 0 ? "+" : ""}{groupPnl.toFixed(2)} u
                            </span>
                          </summary>
                          <div className="px-3 pb-3 pt-1 space-y-1">
                            {group.map((b) => (
                              <div key={b.id} className="flex items-center gap-3 text-sm py-1">
                                <span className="px-2 py-0.5 bg-slate-500/20 text-slate-300 rounded text-xs">
                                  {marketShort(b.market)}
                                </span>
                                <span className="text-muted">{selectionLabel(b.market, b.selection, b.line)}</span>
                                <span className="text-muted">@ {Number(b.oddsTaken).toFixed(2)}</span>
                                <span className={`ml-auto font-medium ${b.status === "won" ? "text-green-400" : "text-red-400"}`}>
                                  {b.status === "won" ? "Gagné" : "Perdu"} · {Number(b.pnlUnits) >= 0 ? "+" : ""}{Number(b.pnlUnits).toFixed(2)} u
                                </span>
                              </div>
                            ))}
                          </div>
                        </details>
                      );
                    })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
