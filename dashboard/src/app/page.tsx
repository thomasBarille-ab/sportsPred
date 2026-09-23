import { getLatestSummary, getOverviewStats } from "@/lib/db";
import Link from "next/link";

export const dynamic = "force-dynamic";
export const revalidate = 0;

async function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className="stat-value mt-2">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

export default async function Home() {
  const [l1, nba, summary] = await Promise.all([
    getOverviewStats("ligue1", 30),
    getOverviewStats("nba", 30),
    getLatestSummary(),
  ]);

  const agentReport = summary?.agentReport as {
    summary?: string;
    patterns?: string[];
    recommendations?: string[];
    retrain_recommended?: boolean;
    retrain_reason?: string | null;
  } | null ?? null;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Vue d&apos;ensemble</h1>

      {/* ── Insights Agent Claude ────────────────────────────────────────────── */}
      {agentReport && (
        <div className="card border-violet-500/30 bg-violet-500/5 space-y-4">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-violet-400 uppercase tracking-wider">
              Insights Agent Claude
            </span>
            {summary?.summaryDate && (
              <span className="text-xs text-muted">
                — {new Date(summary.summaryDate).toLocaleDateString("fr-FR")}
              </span>
            )}
            {agentReport.retrain_recommended && (
              <span className="ml-auto text-xs bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded font-medium">
                Retrain recommandé
              </span>
            )}
          </div>

          {agentReport.summary && (
            <p className="text-sm leading-relaxed text-slate-200">{agentReport.summary}</p>
          )}

          {agentReport.patterns && agentReport.patterns.length > 0 && (
            <div>
              <div className="text-xs text-muted mb-2 font-medium">Patterns détectés</div>
              <ul className="space-y-1.5">
                {agentReport.patterns.map((p, i) => (
                  <li key={i} className="text-sm text-slate-300 flex gap-2">
                    <span className="text-violet-400 shrink-0">›</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {agentReport.recommendations && agentReport.recommendations.length > 0 && (
            <div>
              <div className="text-xs text-muted mb-2 font-medium">Recommandations</div>
              <ul className="space-y-1.5">
                {agentReport.recommendations.map((r, i) => (
                  <li key={i} className="text-sm text-slate-300 flex gap-2">
                    <span className="text-emerald-400 shrink-0">→</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {agentReport.retrain_recommended && agentReport.retrain_reason && (
            <p className="text-xs text-amber-300 bg-amber-500/10 rounded px-3 py-2">
              {agentReport.retrain_reason}
            </p>
          )}
        </div>
      )}

      {/* ── Résumé Llama ────────────────────────────────────────────────────── */}
      {summary?.content && (
        <div className="card border-accent/40 bg-accent/5">
          <div className="text-xs text-muted mb-2">
            Résumé IA — {new Date(summary.summaryDate).toLocaleDateString("fr-FR")}
          </div>
          <p className="text-sm leading-relaxed text-slate-200">{summary.content}</p>
        </div>
      )}

      {/* ── Stats 30 derniers jours ──────────────────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold mb-4 text-muted">Ligue 1 — 30 derniers jours</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Prédictions scorées" value={String(l1?.total ?? 0)} />
          <StatCard label="Accuracy" value={`${((Number(l1?.accuracy) || 0) * 100).toFixed(1)}%`} />
          <StatCard label="Brier score moyen" value={l1?.avgBrier ?? "—"} sub="↓ meilleur · baseline 0.667" />
          <StatCard label="Log-loss moyen" value={l1?.avgLogloss ?? "—"} sub="↓ meilleur" />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-4 text-muted">NBA — 30 derniers jours</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Prédictions scorées" value={String(nba?.total ?? 0)} />
          <StatCard label="Accuracy" value={`${((Number(nba?.accuracy) || 0) * 100).toFixed(1)}%`} />
          <StatCard label="Brier score moyen" value={nba?.avgBrier ?? "—"} sub="↓ meilleur · baseline 0.25" />
          <StatCard label="Log-loss moyen" value={nba?.avgLogloss ?? "—"} sub="↓ meilleur" />
        </div>
      </section>

      {/* ── Liens rapides ──────────────────────────────────────────────────── */}
      <div className="flex gap-4 flex-wrap">
        {[
          { href: "/ligue1", label: "Détail Ligue 1" },
          { href: "/nba", label: "Détail NBA" },
          { href: "/models", label: "Historique modèles" },
          { href: "/logs", label: "Logs agent" },
        ].map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="px-4 py-2 rounded-lg bg-accent/20 hover:bg-accent/30 text-accent text-sm font-medium transition-colors"
          >
            {l.label} →
          </Link>
        ))}
      </div>
    </div>
  );
}
