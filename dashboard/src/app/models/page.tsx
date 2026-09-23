import { getModelVersions, getFeatureImportances } from "@/lib/db";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

export const dynamic = "force-dynamic";

export default async function ModelsPage() {
  const [l1Models, nbaModels, l1Importances, nbaImportances] = await Promise.all([
    getModelVersions("ligue1"),
    getModelVersions("nba"),
    getFeatureImportances("ligue1"),
    getFeatureImportances("nba"),
  ]);

  const ModelTable = ({ models, sport }: { models: any[]; sport: string }) => (
    <div className="card">
      <h2 className="text-base font-semibold mb-4">{sport}</h2>
      {models.length === 0 ? (
        <p className="text-muted text-sm">Aucun modèle entraîné pour l&apos;instant.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left py-2 pr-4">Version</th>
                <th className="text-left py-2 pr-4">Entraîné le</th>
                <th className="text-right py-2 pr-4">Train samples</th>
                <th className="text-right py-2 pr-4">Holdout</th>
                <th className="text-right py-2 pr-4">Brier ↓</th>
                <th className="text-right py-2 pr-4">Log-loss ↓</th>
                <th className="text-right py-2 pr-4">Accuracy ↑</th>
                <th className="text-center py-2">Statut</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m: any) => (
                <tr key={m.id} className="border-b border-border/50 hover:bg-white/5">
                  <td className="py-2 pr-4 font-mono text-xs text-slate-300">{m.version}</td>
                  <td className="py-2 pr-4 text-muted">
                    {format(new Date(m.trainedAt), "dd MMM yyyy HH:mm", { locale: fr })}
                  </td>
                  <td className="py-2 pr-4 text-right text-muted">{m.trainingSamples ?? "—"}</td>
                  <td className="py-2 pr-4 text-right text-muted">{m.holdoutSamples ?? "—"}</td>
                  <td className="py-2 pr-4 text-right font-mono">{m.holdoutBrier ?? "—"}</td>
                  <td className="py-2 pr-4 text-right font-mono">{m.holdoutLogloss ?? "—"}</td>
                  <td className="py-2 pr-4 text-right font-mono">
                    {m.holdoutAccuracy ? `${(m.holdoutAccuracy * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="py-2 text-center">
                    {m.isProduction ? (
                      <span className="badge-success">production</span>
                    ) : (
                      <span className="badge-neutral">archivé</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  const ImportanceChart = ({
    importances,
    sport,
  }: {
    importances: { feature_name: string; importance: number }[];
    sport: string;
  }) => {
    if (importances.length === 0) return null;
    const max = importances[0].importance;
    return (
      <div className="card">
        <h2 className="text-base font-semibold mb-4">Feature importances — {sport} (modèle en production)</h2>
        <div className="space-y-1.5">
          {importances.map((row) => (
            <div key={row.feature_name} className="flex items-center gap-3 text-xs">
              <span className="w-48 shrink-0 text-right text-muted font-mono truncate">
                {row.feature_name}
              </span>
              <div className="flex-1 bg-slate-800 rounded-full h-2 overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full"
                  style={{ width: `${(row.importance / max) * 100}%` }}
                />
              </div>
              <span className="w-12 text-right font-mono text-slate-400">
                {(row.importance * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold">Historique des modèles</h1>
      <p className="text-sm text-muted">
        Un nouveau modèle n&apos;est promu en production que si son Brier score holdout
        améliore le champion actuel d&apos;au moins 0.002.
      </p>
      <p className="text-xs text-muted">
        Les modèles sans <code>pipeline_version ≥ 2</code> utilisent un ancien pipeline
        (Elo/DC non walk-forward) et une échelle Brier différente — leurs métriques ne sont
        pas comparables aux modèles récents.
      </p>
      <ModelTable models={l1Models as any[]} sport="Ligue 1" />
      <ModelTable models={nbaModels as any[]} sport="NBA" />
      <ImportanceChart importances={l1Importances as any[]} sport="Ligue 1" />
      <ImportanceChart importances={nbaImportances as any[]} sport="NBA" />
    </div>
  );
}
