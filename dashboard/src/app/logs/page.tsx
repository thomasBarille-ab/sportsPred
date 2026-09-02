import { getAgentLogs } from "@/lib/db";
import LogEntry from "@/components/LogEntry";
import TriggerButton from "@/components/TriggerButton";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const STATUS_STYLES: Record<string, string> = {
  success: "text-emerald-400",
  failed: "text-red-400",
  running: "text-yellow-400",
};

const JOBS = ["ingest", "predict", "evaluate", "retrain"];

export default async function LogsPage() {
  const logs = await getAgentLogs(100);

  const counts = (logs as any[]).reduce(
    (acc: Record<string, number>, l: any) => {
      acc[l.status] = (acc[l.status] ?? 0) + 1;
      return acc;
    },
    {}
  );

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-6">
        <div>
          <h1 className="text-2xl font-bold">Logs de l&apos;agent</h1>
          <div className="flex gap-4 mt-1 text-sm">
            {Object.entries(counts).map(([status, n]) => (
              <span key={status} className={STATUS_STYLES[status] ?? "text-muted"}>
                {n} {status}
              </span>
            ))}
          </div>
        </div>

        {/* Boutons de déclenchement manuel */}
        <div className="flex-shrink-0">
          <p className="text-xs text-muted mb-2">Déclencher manuellement :</p>
          <div className="flex flex-wrap gap-2">
            {JOBS.map((job) => (
              <TriggerButton key={job} job={job} />
            ))}
          </div>
        </div>
      </div>

      <p className="text-xs text-muted">
        Les logs avec des étapes détaillées (▼) apparaissent uniquement pour les runs effectués après la mise à jour du predictor.
        Cliquez sur une ligne pour replier ses étapes.
      </p>

      <div className="space-y-2">
        {(logs as any[]).map((l: any) => (
          <LogEntry key={l.id} log={l} />
        ))}
        {logs.length === 0 && (
          <div className="card text-center text-muted py-12">
            Aucun log disponible — les jobs n&apos;ont pas encore tourné.
          </div>
        )}
      </div>
    </div>
  );
}
