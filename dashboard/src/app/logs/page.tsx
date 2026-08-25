import { getAgentLogs } from "@/lib/db";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function LogsPage() {
  const logs = await getAgentLogs(100);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Logs de l&apos;agent</h1>

      <div className="card">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left py-2 pr-4">Job</th>
                <th className="text-left py-2 pr-4">Sport</th>
                <th className="text-left py-2 pr-4">Démarré</th>
                <th className="text-right py-2 pr-4">Durée</th>
                <th className="text-right py-2 pr-4">Records</th>
                <th className="text-center py-2 pr-4">Statut</th>
                <th className="text-left py-2">Erreur</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((l: any) => (
                <tr key={l.id} className="border-b border-border/50 hover:bg-white/5">
                  <td className="py-2 pr-4 font-mono text-xs">{l.jobName}</td>
                  <td className="py-2 pr-4 text-muted">{l.sport ?? "—"}</td>
                  <td className="py-2 pr-4 text-muted">
                    {format(new Date(l.startedAt), "dd/MM HH:mm:ss", { locale: fr })}
                  </td>
                  <td className="py-2 pr-4 text-right text-muted">
                    {l.durationSeconds != null ? `${l.durationSeconds.toFixed(1)}s` : "—"}
                  </td>
                  <td className="py-2 pr-4 text-right text-muted">
                    {l.recordsProcessed ?? "—"}
                  </td>
                  <td className="py-2 pr-4 text-center">
                    {l.status === "success" ? (
                      <span className="badge-success">success</span>
                    ) : l.status === "failed" ? (
                      <span className="badge-danger">failed</span>
                    ) : (
                      <span className="badge-neutral">running</span>
                    )}
                  </td>
                  <td className="py-2 text-xs text-danger truncate max-w-xs">
                    {l.errorMessage ? l.errorMessage.slice(0, 120) : "—"}
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
