"use client";

import { useState, useRef, useEffect, FormEvent } from "react";

type Role = "user" | "assistant";

type Message = {
  role: Role;
  content: string;
  action?: string | null;
  action_note?: string | null;
  error?: boolean;
};

const ACTION_LABELS: Record<string, string> = {
  ingest:   "Ingestion déclenchée",
  predict:  "Prédiction déclenchée",
  evaluate: "Évaluation déclenchée",
  retrain:  "Réentraînement déclenché",
  summary:  "Résumé généré",
};

const SUGGESTIONS = [
  "Quelles sont les performances actuelles du modèle Ligue 1 ?",
  "Explique pourquoi le modèle est calibré ou pas",
  "Quels sont les derniers matchs évalués ?",
  "/ingest — récupérer les données récentes",
  "/predict — générer les prédictions",
  "/evaluate — scorer les prédictions",
  "/retrain — réentraîner les modèles",
  "/summary — générer le résumé",
];

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content:
        "Bonjour ! Je suis l'agent de prédiction sportive. Je peux vous renseigner sur les performances des modèles et les prédictions en cours.\n\nPour déclencher un job, envoyez une commande exacte :\n  /ingest · /predict · /evaluate · /retrain · /summary",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef  = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || loading) return;

    const userMsg: Message = { role: "user", content };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    // Historique envoyé à l'API (rôles user/assistant uniquement)
    const history = messages
      .filter((m) => !m.error)
      .map(({ role, content }) => ({ role, content }));

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: content, history }),
      });
      const data = await res.json();

      if (!res.ok) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: data.error ?? "Erreur inconnue", error: true },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: data.response,
            action: data.action,
            action_note: data.action_note,
          },
        ]);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Erreur réseau : ${err}`, error: true },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <h1 className="text-2xl font-bold mb-4 flex-shrink-0">Agent</h1>

      {/* Zone de messages */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-4 pr-1">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[75%] ${
                msg.role === "user"
                  ? "bg-blue-600/30 border border-blue-500/30 text-slate-100"
                  : msg.error
                  ? "bg-red-500/10 border border-red-500/20 text-red-300"
                  : "bg-card border border-border text-slate-200"
              } rounded-2xl px-4 py-3 text-sm leading-relaxed`}
            >
              {/* Badge action déclenchée */}
              {msg.action && (
                <div className="mb-2 px-2 py-1 rounded bg-emerald-500/15 text-emerald-400 text-xs font-medium inline-flex items-center gap-1.5">
                  <span>▶</span>
                  <span>{ACTION_LABELS[msg.action] ?? msg.action}</span>
                  {msg.action_note && (
                    <span className="text-emerald-600 font-normal">— {msg.action_note}</span>
                  )}
                </div>
              )}

              {/* Texte avec sauts de ligne */}
              <div className="whitespace-pre-wrap">{msg.content}</div>
            </div>
          </div>
        ))}

        {/* Indicateur de chargement */}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-card border border-border rounded-2xl px-4 py-3">
              <div className="flex gap-1 items-center h-4">
                <span className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Suggestions (uniquement au début) */}
      {messages.length === 1 && !loading && (
        <div className="flex-shrink-0 flex flex-wrap gap-2 pb-3">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => send(s)}
              className="text-xs px-3 py-1.5 rounded-full border border-border text-muted hover:text-white hover:border-slate-500 transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Zone de saisie */}
      <form onSubmit={onSubmit} className="flex-shrink-0 flex gap-3 items-end pt-2 border-t border-border">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Posez une question ou donnez un ordre… (Entrée pour envoyer, Maj+Entrée pour nouvelle ligne)"
          rows={1}
          className="flex-1 bg-card border border-border rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-600 resize-none focus:outline-none focus:border-slate-500 transition-colors"
          style={{ minHeight: "48px", maxHeight: "140px" }}
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="px-4 py-3 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 text-white text-sm font-medium transition-colors flex-shrink-0"
        >
          {loading ? "…" : "Envoyer"}
        </button>
      </form>
    </div>
  );
}
