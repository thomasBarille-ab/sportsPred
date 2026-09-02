import { getAgentLogs } from "@/lib/db";
import LogEntry from "@/components/LogEntry";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const STATUS_COUNT_STYLES: Record<string, string> = {
  success: "text-emerald-400",
  failed: "text-red-400",
  running: "text-yellow-400",
};

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
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Logs de l&apos;agent</h1>
        <div className="flex gap-4 text-sm">
          {Object.entries(counts).map(([status, n]) => (
            <span key={status} className={STATUS_COUNT_STYLES[status] ?? "text-muted"}>
              {n} {status}
            </span>
          ))}
        </div>
      </div>

      <p className="text-xs text-muted">
        Cliquez sur un run pour dérouler ses étapes détaillées.
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
