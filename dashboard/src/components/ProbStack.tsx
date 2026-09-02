type ProbEntry = { label: string; value: number };

export default function ProbStack({ probs }: { probs: ProbEntry[] }) {
  const max = Math.max(...probs.map((p) => p.value));
  return (
    <div className="space-y-0.5 min-w-[130px]">
      {probs.map(({ label, value }) => {
        const isMax = value === max;
        return (
          <div key={label} className="flex items-center gap-1.5">
            <span className="text-[10px] text-muted w-6 flex-shrink-0">{label}</span>
            <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${isMax ? "bg-blue-500" : "bg-slate-600"}`}
                style={{ width: `${value * 100}%` }}
              />
            </div>
            <span
              className={`text-[11px] w-8 text-right flex-shrink-0 tabular-nums ${
                isMax ? "text-white font-semibold" : "text-muted"
              }`}
            >
              {(value * 100).toFixed(0)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}
