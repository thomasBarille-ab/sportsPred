import { NextRequest, NextResponse } from "next/server";

const PREDICTOR_URL = process.env.PREDICTOR_URL ?? "http://predictor:8080";

const ALLOWED_JOBS = ["ingest", "predict", "evaluate", "retrain", "summary"];

export async function POST(
  _req: NextRequest,
  { params }: { params: { job: string } }
) {
  const { job } = params;

  if (!ALLOWED_JOBS.includes(job)) {
    return NextResponse.json({ error: "job inconnu" }, { status: 400 });
  }

  try {
    const res = await fetch(`${PREDICTOR_URL}/run/${job}`, {
      method: "POST",
      signal: AbortSignal.timeout(5000),
    });
    const body = await res.json();
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: "Impossible de joindre le predictor", detail: String(err) },
      { status: 502 }
    );
  }
}
