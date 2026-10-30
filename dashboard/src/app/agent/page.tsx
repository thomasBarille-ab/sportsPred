import { getAgentPredictions, getAgentStats } from "@/lib/db";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

export const dynamic = "force-dynamic";

const SPORT_LABEL: Record<string, string> = { ligue1: "L1", nba: "NBA" };
const OUTCOME_LABEL: Record<string, string> = { home: "Dom.", draw: "Nul", away: "Ext." };
const OUTCOME_COLOR: Record<string, string> = {
  home: "bg-blue-500/20 text-blue-300",
  draw: "bg-slate-500/20 text-slate-300",
  away: "bg-orange-500/20 text-orange-300",
};

type StatRow = {
  sport: string;
  agentTotal: number;
  agentScored: number;
  agentCorrect: number;
  agentAccuracy: number | null;
  agentBrier: number | null;
  mlScored: number;
  mlAccuracy: number | null;
  mlBrier: number | null;
};

type PredRow = {
  fixtureId: number;
  sport: string;
  homeTeamName: string;
  awayTeamName: string;
  matchDate: Date;
  agentOutcome: string;
  confidence: number;
  reasoning: string;
  modelName: string;
  mlOutcome: string | null;
  actualOutcome: string | null;
  homeScore: number | null;
  awayScore: number | null;
  agentCorrect: boolean | null;
  agentBrier: number | null;
  mlCorrect: boolean | null;
  mlBrier: number | null;
};

function VerdictBadge({ correct }: { correct: boolean | null }) {
  if (correct === null) return <span className="text-muted text-xs">—</span>;
  return correct
    ? <span className="text-emerald-400 font-bold">✓</span>
    : <span className="text-red-400 font-bold">✗</span>;
}

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="text-muted text-xs">—</span>;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${OUTCOME_COLOR[outcome] ?? "bg-slate-600/40 text-slate-300"}`}>
      {OUTCOME_LABEL[outcome] ?? outcome}
    </span>
  );
}

function DeltaBadge({ agent, ml }: { agent: number | null; ml: number | null }) {
  if (agent == null || ml == null) return <span className="text-muted text-xs">—</span>;
  const delta = (agent - ml) * 100;
  const positive = delta > 0;
  return (
    <span className={`text-xs font-medium ${positive ? "text-green-400" : "text-red-400"}`}>
      {positive ? "+" : ""}{delta.toFixed(1)}%
    </span>
  );
}

export default async function AgentPage() {
  const [statsRaw, predsRaw] = await Promise.all([
    getAgentStats().catch(() => []),
    getAgentPredictions(150).catch(() => []),
  ]);

  const stats = statsRaw as StatRow[];
  const preds = predsRaw as PredRow[];

  const scoredPreds = preds.filter((p) => p.agentCorrect !== null);
  const disagreements = scoredPreds.filter(
    (p) => p.agentOutcome !== p.mlOutcome && p.mlOutcome !== null
  );
  const agentWonDisagreement = disagreements.filter((p) => p.agentCorrect && !p.mlCorrect).length;
  const mlWonDisagreement = disagreements.filter((p) => !p.agentCorrect && p.mlCorrect).length;

  return (
    <div className="p-8 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-white">Agent LLM — Prédictions indépendantes</h1>
        <p className="text-sm text-muted mt-1">
          Prédictions basées sur recherche web + contexte DB, sans modèle ML. Comparées aux modèles existants.
        </p>
      </div>

      {/* Stats par sport */}
      {stats.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {stats.map((s) => {
            const agentAcc = s.agentAccuracy != null ? Number(s.agentAccuracy) : null;
            const mlAcc = s.mlAccuracy != null ? Number(s.mlAccuracy) : null;
            const delta = agentAcc != null && mlAcc != null ? agentAcc - mlAcc : null;

            return (
              <div key={s.sport} className="bg-card border border-border rounded-xl p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-white uppercase tracking-wide">
                    {SPORT_LABEL[s.sport] ?? s.sport}
                  </h2>
                  <span className="text-xs text-muted">
                    {Number(s.agentScored)} scorés / {Number(s.agentTotal)} prédit{Number(s.agentTotal) > 1 ? "s" : ""}
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-3 text-center">
                  <div>
                    <p className="text-xs text-muted mb-1">Agent</p>
                    <p className={`text-xl font-bold ${agentAcc != null && agentAcc >= 0.5 ? "text-emerald-400" : "text-red-400"}`}>
                      {agentAcc != null ? `${Math.round(agentAcc * 100)}%` : "—"}
                    </p>
                    <p className="text-xs text-muted">{s.agentBrier != null ? `Brier ${Number(s.agentBrier).toFixed(4)}` : ""}</p>
                  </div>
                  <div className="flex flex-col items-center justify-center">
                    <p className="text-xs text-muted mb-1">Δ accuracy</p>
                    {delta != null ? (
                      <p className={`text-lg font-bold ${delta >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {delta >= 0 ? "+" : ""}{(delta * 100).toFixed(1)}%
                      </p>
                    ) : (
                      <p className="text-lg text-muted">—</p>
                    )}
                  </div>
                  <div>
                    <p className="text-xs text-muted mb-1">ML</p>
                    <p className="text-xl font-bold text-white">
                      {mlAcc != null ? `${Math.round(mlAcc * 100)}%` : "—"}
                    </p>
                    <p className="text-xs text-muted">{s.mlBrier != null ? `Brier ${Number(s.mlBrier).toFixed(4)}` : ""}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Désaccords agent vs ML */}
      {scoredPreds.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-card border border-border rounded-xl p-4 text-center">
            <p className="text-xs text-muted mb-1">Désaccords agent/ML</p>
            <p className="text-2xl font-bold text-white">{disagreements.length}</p>
          </div>
          <div className="bg-card border border-border rounded-xl p-4 text-center">
            <p className="text-xs text-muted mb-1">Agent gagne la mise</p>
            <p className="text-2xl font-bold text-emerald-400">{agentWonDisagreement}</p>
          </div>
          <div className="bg-card border border-border rounded-xl p-4 text-center">
            <p className="text-xs text-muted mb-1">ML gagne la mise</p>
            <p className="text-2xl font-bold text-blue-400">{mlWonDisagreement}</p>
          </div>
        </div>
      )}

      {/* Tableau des prédictions */}
      <div className="bg-card border border-border rounded-xl p-6">
        <h2 className="text-sm font-semibold text-white mb-4">
          Toutes les prédictions
          <span className="text-muted font-normal ml-2">({preds.length})</span>
        </h2>

        {preds.length === 0 ? (
          <p className="text-muted text-sm">
            Aucune prédiction agent pour l&apos;instant — le job tourne toutes les 30 min, 1h avant chaque match.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted text-xs uppercase tracking-wide border-b border-border">
                  <th className="text-left py-2 pr-4">Match</th>
                  <th className="text-left py-2 pr-4">Sport</th>
                  <th className="text-center py-2 pr-4">Agent</th>
                  <th className="text-center py-2 pr-4">Confiance</th>
                  <th className="text-center py-2 pr-4">ML</th>
                  <th className="text-center py-2 pr-4">Résultat</th>
                  <th className="text-center py-2 pr-2">Agent</th>
                  <th className="text-center py-2 pr-4">ML</th>
                  <th className="text-right py-2 pr-4">Brier agent</th>
                  <th className="text-right py-2 pr-4">Δ vs ML</th>
                  <th className="text-left py-2">Raisonnement</th>
                </tr>
              </thead>
              <tbody>
                {preds.map((p) => {
                  const matchDate = new Date(p.matchDate);
                  const disagree = p.mlOutcome !== null && p.agentOutcome !== p.mlOutcome;
                  const rowBg = p.agentCorrect === true
                    ? "bg-emerald-500/5"
                    : p.agentCorrect === false
                    ? "bg-red-500/5"
                    : "";

                  return (
                    <tr
                      key={p.fixtureId}
                      className={`border-b border-border/40 hover:bg-white/5 transition-colors ${rowBg}`}
                    >
                      <td className="py-2.5 pr-4 font-medium whitespace-nowrap">
                        {p.homeTeamName}{" "}
                        <span className="text-muted text-xs">vs</span>{" "}
                        {p.awayTeamName}
                        <span className="block text-xs text-muted">
                          {format(matchDate, "dd MMM yyyy HH:mm", { locale: fr })}
                        </span>
                      </td>
                      <td className="py-2.5 pr-4 text-muted text-xs">
                        {SPORT_LABEL[p.sport] ?? p.sport}
                      </td>
                      <td className="py-2.5 pr-4 text-center">
                        <OutcomeBadge outcome={p.agentOutcome} />
                      </td>
                      <td className="py-2.5 pr-4 text-center text-muted text-xs">
                        {p.confidence}%
                      </td>
                      <td className="py-2.5 pr-4 text-center">
                        {disagree ? (
                          <span className="ring-1 ring-yellow-500/50 rounded">
                            <OutcomeBadge outcome={p.mlOutcome} />
                          </span>
                        ) : (
                          <OutcomeBadge outcome={p.mlOutcome} />
                        )}
                      </td>
                      <td className="py-2.5 pr-4 text-center">
                        {p.actualOutcome ? (
                          <OutcomeBadge outcome={p.actualOutcome} />
                        ) : (
                          <span className="text-muted text-xs">À jouer</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-2 text-center">
                        <VerdictBadge correct={p.agentCorrect} />
                      </td>
                      <td className="py-2.5 pr-4 text-center">
                        <VerdictBadge correct={p.mlCorrect} />
                      </td>
                      <td className="py-2.5 pr-4 text-right text-muted text-xs font-mono tabular-nums">
                        {p.agentBrier != null ? Number(p.agentBrier).toFixed(4) : "—"}
                      </td>
                      <td className="py-2.5 pr-4 text-right">
                        <DeltaBadge
                          agent={p.agentBrier != null ? Number(p.agentBrier) : null}
                          ml={p.mlBrier != null ? Number(p.mlBrier) : null}
                        />
                      </td>
                      <td className="py-2.5 max-w-xs">
                        <details className="cursor-pointer">
                          <summary className="text-xs text-slate-400 hover:text-white list-none truncate max-w-[200px]">
                            {p.reasoning.slice(0, 60)}…
                          </summary>
                          <p className="text-xs text-slate-300 mt-1 whitespace-normal">
                            {p.reasoning}
                          </p>
                        </details>
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
