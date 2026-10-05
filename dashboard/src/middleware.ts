import { NextRequest, NextResponse } from "next/server";
import { requireAccess } from "@/lib/auth";

export async function middleware(req: NextRequest) {
  const error = await requireAccess(req);
  if (error) return error;
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
