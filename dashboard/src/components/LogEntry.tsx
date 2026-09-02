"use client";

import { useState } from "react";
import { format } from "date-fns";
import { fr } from "date-fns/locale";

const JOB_LABELS: Record<string, string> = {
  ingest: "Ingestion",
  predict: "Prédiction",
  evaluate: "Évaluation",
  retrain: "Réentraînement",
  summary: "Résumé",
};

const SPORT_LABELS: Record<string, string> = {
  ligue1: "Ligue 1",
  nba: "NBA",
};

const LEVEL_STYLES: Record<string, string> = {
  info: "text-slate-400",
  warning: "text-yellow-400",
  error: "text-red-400",
  critical: "text-red-500",
  debug: "text-slate-600",
};

type Step = {
  ts: string;
  level: string;
  event: string;
  [key: string]: unknown;
};

type Log = {
  id: number;
  jobName: string;
  sport: string | null;
  startedAt: string;
  finishedAt: string | null;
  status: string;
  durationSeconds: number | null;
  recordsProcessed: number | null;
  errorMessage: string | null;
  details: { steps?: Step[] } | null;
};

export default function LogEntry({ log }: { log: Log }) {
  const steps: Step[] = log.details?.steps ?? [];
  const hasSteps = steps.length > 0;
  const [expanded, setExpanded] = useState(hasSteps);

  const statusDot =
    log.status === "success"
      ? "bg-emerald-500"
      : log.status === "failed"
      ? "bg-red-500"
      : "bg-yellow-400 animate-pulse";

  const statusBadge =
    log.status === "success"
      ? "bg-emerald-500/15 text-emerald-400"
      : log.status === "failed"
      ? "bg-red-500/15 text-red-400"
      : "bg-yellow-500/15 text-yellow-400";

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/5 text-left transition-colors"
        onClick={() => hasSteps && setExpanded(!expanded)}
        style={{ cursor: hasSteps ? "pointer" : "default" }}
      >
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusDot}`} />

        <span className="font-semibold text-sm w-36 flex-shrink-0 text-slate-100">
          {JOB_LABELS[log.jobName] ?? log.jobName}
        </span>

        <span className="text-xs text-muted w-16 flex-shrink-0">
          {log.sport ? (SPORT_LABELS[log.sport] ?? log.sport) : "—"}
        </span>

        <span className="text-xs text-muted flex-1 font-mono">
          {format(new Date(log.startedAt), "dd MMM HH:mm:ss", { locale: fr })}
        </span>

        {log.durationSeconds != null && (
          <span className="text-xs text-muted w-16 text-right flex-shrink-0 font-mono">
            {log.durationSeconds.toFixed(1)}s
          </span>
        )}

        {log.recordsProcessed != null && (
          <span className="text-xs text-muted w-24 text-right flex-shrink-0">
            {log.recordsProcessed} records
          </span>
        )}

        <span className={`text-xs px-2 py-0.5 rounded font-medium flex-shrink-0 ${statusBadge}`}>
          {log.status}
        </span>

        {hasSteps && (
          <span className="text-slate-600 text-xs ml-1 flex-shrink-0">
            {expanded ? "▲" : "▼"} {steps.length}
          </span>
        )}
      </button>

      {log.errorMessage && (
        <div className="px-4 py-2 bg-red-500/10 text-red-400 text-xs font-mono border-t border-border whitespace-pre-wrap break-all">
          {log.errorMessage.slice(0, 400)}
        </div>
      )}

      {!hasSteps && log.status !== "running" && (
        <div className="px-6 py-2 text-xs text-slate-600 border-t border-border/30 italic">
          Aucun détail — ce run date d&apos;avant les logs enrichis.
        </div>
      )}

      {expanded && hasSteps && (
        <div className="border-t border-border bg-black/20">
          {steps.map((step, i) => {
            const extra = Object.entries(step).filter(
              ([k]) => !["ts", "level", "event"].includes(k)
            );
            return (
              <div
                key={i}
                className="flex gap-3 px-6 py-1.5 text-xs border-b border-border/20 last:border-0 hover:bg-white/5"
              >
                <span className="text-slate-600 font-mono w-20 flex-shrink-0">
                  {step.ts ? String(step.ts).slice(11, 19) : ""}
                </span>
                <span
                  className={`w-14 flex-shrink-0 font-medium ${
                    LEVEL_STYLES[step.level] ?? "text-slate-400"
                  }`}
                >
                  {step.level}
                </span>
                <span className="font-mono text-slate-200 w-52 flex-shrink-0">
                  {step.event}
                </span>
                {extra.length > 0 && (
                  <span className="text-slate-500 flex-1 truncate">
                    {extra.map(([k, v]) => `${k}=${v}`).join("  ")}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
