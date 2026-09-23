"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { format } from "date-fns";

type Point = {
  day: string;
  cumulativePnl: number;
};

type Props = { data: Point[] };

export function BankrollChart({ data }: Props) {
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-muted text-sm">
        Aucun pari réglé pour l&apos;instant
      </div>
    );
  }

  const formatted = data.map((d) => ({
    ...d,
    label: format(new Date(d.day), "dd/MM"),
  }));

  const minVal = Math.min(0, ...data.map((d) => d.cumulativePnl));
  const maxVal = Math.max(0, ...data.map((d) => d.cumulativePnl));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={formatted} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
        <XAxis dataKey="label" tick={{ fill: "#888", fontSize: 11 }} />
        <YAxis
          tick={{ fill: "#888", fontSize: 11 }}
          domain={[minVal - 0.5, maxVal + 0.5]}
          tickFormatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`}
        />
        <Tooltip
          contentStyle={{ background: "#1a1a2e", border: "1px solid #2a2a3a", borderRadius: 6 }}
          labelStyle={{ color: "#aaa" }}
          formatter={(v: number) => [`${v > 0 ? "+" : ""}${v.toFixed(2)} u`, "P&L cumulé"]}
        />
        <ReferenceLine y={0} stroke="#444" strokeDasharray="4 2" />
        <Line
          type="monotone"
          dataKey="cumulativePnl"
          stroke="#22c55e"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, fill: "#22c55e" }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
