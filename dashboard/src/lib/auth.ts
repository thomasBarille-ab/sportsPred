import { NextRequest, NextResponse } from "next/server";
import { createRemoteJWKSet, jwtVerify } from "jose";

const CF_TEAM_DOMAIN = process.env.CF_ACCESS_TEAM_DOMAIN ?? "";
const CF_AUD         = process.env.CF_ACCESS_AUD ?? "";
const AUTH_DISABLED  = process.env.AUTH_DISABLED === "true";
const IS_PRODUCTION  = process.env.NODE_ENV === "production";

let jwks: ReturnType<typeof createRemoteJWKSet> | null = null;

function getJwks() {
  if (!jwks && CF_TEAM_DOMAIN) {
    jwks = createRemoteJWKSet(
      new URL(`https://${CF_TEAM_DOMAIN}/cdn-cgi/access/certs`)
    );
  }
  return jwks;
}

/**
 * Vérifie le JWT Cloudflare Access (Cf-Access-Jwt-Assertion).
 * Retourne null si l'accès est autorisé, ou une NextResponse d'erreur.
 *
 * Comportement :
 *   - AUTH_DISABLED=true en dev : toujours autorisé (ne pas utiliser en prod)
 *   - Variables CF_* absentes en production : 503
 *   - JWT invalide : 401
 */
export async function requireAccess(
  req: NextRequest
): Promise<NextResponse | null> {
  // Dev local sans auth
  if (AUTH_DISABLED && !IS_PRODUCTION) {
    return null;
  }

  if (!CF_TEAM_DOMAIN || !CF_AUD) {
    return NextResponse.json(
      { error: "Cloudflare Access non configuré (CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD manquants)" },
      { status: 503 }
    );
  }

  const token = req.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) {
    return NextResponse.json({ error: "Token Cloudflare Access manquant" }, { status: 401 });
  }

  try {
    await jwtVerify(token, getJwks()!, {
      issuer:   `https://${CF_TEAM_DOMAIN}`,
      audience: CF_AUD,
    });
    return null;
  } catch {
    return NextResponse.json({ error: "Token Cloudflare Access invalide" }, { status: 401 });
  }
}
