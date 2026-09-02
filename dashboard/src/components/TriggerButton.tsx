"use client";

import { useState } from "react";

const JOB_LABELS: Record<string, string> = {
  ingest: "Ingestion",
  predict: "Prédiction",
  evaluate: "Évaluation",
  retrain: "Réentraînement",
  summary: "Résumé",
};

export default function TriggerButton({ job }: { job: string }) {
  const [state, setState] = useState<"idle" | "loading" | "ok" | "error">("idle");

  async function trigger() {
    setState("loading");
    try {
      const res = await fetch(`/api/trigger/${job}`, { method: "POST" });
      setState(res.ok ? "ok" : "error");
    } catch {
      setState("error");
    }
    setTimeout(() => setState("idle"), 3000);
  }

  const label = JOB_LABELS[job] ?? job;

  if (state === "loading") {
    return (
      <button disabled className="px-3 py-1.5 rounded text-xs bg-slate-700 text-slate-400 cursor-wait">
        Lancement…
      </button>
    );
  }
  if (state === "ok") {
    return (
      <button disabled className="px-3 py-1.5 rounded text-xs bg-emerald-500/20 text-emerald-400">
        ✓ {label} lancé
      </button>
    );
  }
  if (state === "error") {
    return (
      <button disabled className="px-3 py-1.5 rounded text-xs bg-red-500/20 text-red-400">
        ✗ Erreur
      </button>
    );
  }

  return (
    <button
      onClick={trigger}
      className="px-3 py-1.5 rounded text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white transition-colors border border-border"
    >
      ▶ {label}
    </button>
  );
}
