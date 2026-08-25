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

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Vue d&apos;ensemble</h1>

      {/* ── Résumé Llama ────────────────────────────────────────────────────── */}
      {summary && (
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
          <StatCard label="Brier score moyen" value={l1?.avgBrier ?? "—"} sub="↓ meilleur" />
          <StatCard label="Log-loss moyen" value={l1?.avgLogloss ?? "—"} sub="↓ meilleur" />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-4 text-muted">NBA — 30 derniers jours</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Prédictions scorées" value={String(nba?.total ?? 0)} />
          <StatCard label="Accuracy" value={`${((Number(nba?.accuracy) || 0) * 100).toFixed(1)}%`} />
          <StatCard label="Brier score moyen" value={nba?.avgBrier ?? "—"} sub="↓ meilleur" />
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
