import { NextRequest, NextResponse } from "next/server";

const PREDICTOR_URL = process.env.PREDICTOR_URL ?? "http://predictor:8080";

export async function POST(req: NextRequest) {
  const body = await req.json();

  try {
    const res = await fetch(`${PREDICTOR_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(130_000), // Ollama peut prendre ~2 min
    });

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: "Predictor injoignable", detail: String(err) },
      { status: 502 }
    );
  }
}
