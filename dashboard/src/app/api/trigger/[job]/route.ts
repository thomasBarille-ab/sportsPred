import { NextRequest, NextResponse } from "next/server";
import { requireAccess } from "@/lib/auth";

const PREDICTOR_URL = process.env.PREDICTOR_URL ?? "http://predictor:8080";
const INTERNAL_TOKEN = process.env.INTERNAL_API_TOKEN ?? "";

const ALLOWED_JOBS = ["ingest", "predict", "evaluate", "retrain", "summary", "agent_analysis", "odds_ingest", "odds_backfill", "bet_simulation", "bet_backfill"];

export async function POST(
  req: NextRequest,
  { params }: { params: { job: string } }
) {
  const authError = await requireAccess(req);
  if (authError) return authError;

  const { job } = params;

  if (!ALLOWED_JOBS.includes(job)) {
    return NextResponse.json({ error: "job inconnu" }, { status: 400 });
  }

  try {
    const res = await fetch(`${PREDICTOR_URL}/run/${job}`, {
      method: "POST",
      headers: { "X-Internal-Token": INTERNAL_TOKEN },
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
