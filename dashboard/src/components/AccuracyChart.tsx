"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { format } from "date-fns";

interface Row {
  week: string;
  accuracy: number;
  avgBrier: number;
  n: number;
}

export default function AccuracyChart({ data }: { data: Row[] }) {
  const formatted = data.map((r) => ({
    ...r,
    weekLabel: format(new Date(r.week), "dd/MM"),
    accuracy: Number((r.accuracy * 100).toFixed(1)),
    avgBrier: Number(r.avgBrier),
  }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={formatted} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis dataKey="weekLabel" stroke="#94a3b8" tick={{ fontSize: 12 }} />
        <YAxis
          yAxisId="acc"
          domain={[0, 100]}
          tickFormatter={(v) => `${v}%`}
          stroke="#94a3b8"
          tick={{ fontSize: 12 }}
        />
        <YAxis
          yAxisId="brier"
          orientation="right"
          domain={[0, 0.8]}
          stroke="#94a3b8"
          tick={{ fontSize: 12 }}
        />
        <Tooltip
          contentStyle={{ backgroundColor: "#1e293b", border: "1px solid #334155" }}
          labelStyle={{ color: "#94a3b8" }}
          formatter={(value: number, name: string) =>
            name === "Accuracy" ? [`${value}%`, name] : [value, name]
          }
        />
        <Legend />
        <Line
          yAxisId="acc"
          type="monotone"
          dataKey="accuracy"
          name="Accuracy"
          stroke="#6366f1"
          strokeWidth={2}
          dot={false}
        />
        <Line
          yAxisId="brier"
          type="monotone"
          dataKey="avgBrier"
          name="Brier score"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
