import { NextRequest, NextResponse } from "next/server";
import { requireAccess } from "@/lib/auth";

const PREDICTOR_URL = process.env.PREDICTOR_URL ?? "http://predictor:8080";
const INTERNAL_TOKEN = process.env.INTERNAL_API_TOKEN ?? "";

export async function POST(req: NextRequest) {
  const authError = await requireAccess(req);
  if (authError) return authError;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "JSON invalide" }, { status: 400 });
  }

  if (typeof body !== "object" || body === null) {
    return NextResponse.json({ error: "Body invalide" }, { status: 400 });
  }

  const { message, history } = body as Record<string, unknown>;

  if (typeof message !== "string" || message.trim().length === 0) {
    return NextResponse.json({ error: "message requis" }, { status: 400 });
  }

  const safeMessage = message.trim().slice(0, 2000);
  const wantsStream = req.headers.get("Accept") === "text/event-stream";

  const upstreamHeaders: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Internal-Token": INTERNAL_TOKEN,
  };
  if (wantsStream) upstreamHeaders["Accept"] = "text/event-stream";

  try {
    const res = await fetch(`${PREDICTOR_URL}/chat`, {
      method: "POST",
      headers: upstreamHeaders,
      body: JSON.stringify({ message: safeMessage, history }),
      signal: wantsStream ? undefined : AbortSignal.timeout(130_000),
    });

    if (wantsStream) {
      return new Response(res.body, {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        },
      });
    }

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: "Predictor injoignable", detail: String(err) },
      { status: 502 }
    );
  }
}
