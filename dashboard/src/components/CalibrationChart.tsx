"use client";

import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";

interface CalibRow {
  bucket: number;
  meanPredicted: number;
  actualFreq: number;
  n: number;
}

export default function CalibrationChart({ data }: { data: CalibRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={280}>
      <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis
          dataKey="meanPredicted"
          type="number"
          domain={[0, 1]}
          tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
          name="Probabilité prédite"
          stroke="#94a3b8"
          tick={{ fontSize: 12 }}
        />
        <YAxis
          dataKey="actualFreq"
          type="number"
          domain={[0, 1]}
          tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
          name="Fréquence réelle"
          stroke="#94a3b8"
          tick={{ fontSize: 12 }}
        />
        <Tooltip
          contentStyle={{ backgroundColor: "#1e293b", border: "1px solid #334155" }}
          formatter={(v: number, name: string) => [`${(v * 100).toFixed(1)}%`, name]}
          labelFormatter={() => ""}
          cursor={{ strokeDasharray: "3 3" }}
        />
        {/* Diagonale calibration parfaite */}
        <ReferenceLine
          segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
          stroke="#334155"
          strokeDasharray="4 4"
          label={{ value: "Parfait", fill: "#64748b", fontSize: 11 }}
        />
        <Scatter
          data={data}
          fill="#6366f1"
          opacity={0.85}
        />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
