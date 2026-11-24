import { NextRequest, NextResponse } from "next/server";
import { verifyOwnership, registrableDomain } from "@/lib/ownership";
import { SAMPLE_BYTES, UA } from "@/lib/scanner";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Returns a capped sample of a media URL as PROOF that it was reachable
 * unauthenticated — so the operator can show a reviewer the actual frame/clip.
 *
 * Re-checks ownership here too: the media host must reduce to a domain that
 * passes the live DNS TXT proof. This endpoint cannot be used to proxy
 * content from a domain the operator does not control.
 */
export async function GET(req: NextRequest) {
  const url = req.nextUrl.searchParams.get("url") || "";
  if (!url || !/^https?:\/\//i.test(url)) {
    return NextResponse.json({ error: "Missing or invalid url." }, { status: 400 });
  }

  let host: string;
  try {
    host = new URL(url).hostname;
  } catch {
    return NextResponse.json({ error: "Invalid url." }, { status: 400 });
  }

  const ownership = await verifyOwnership(host);
  if (!ownership.verified) {
    return NextResponse.json(
      {
        error: `Proof refused: ${registrableDomain(host)} is not a verified-owned domain.`,
        code: "OWNERSHIP_REQUIRED",
      },
      { status: 403 }
    );
  }

  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), 15000);
  try {
    const upstream = await fetch(url, {
      signal: controller.signal,
      redirect: "follow",
      headers: { "user-agent": UA, range: `bytes=0-${SAMPLE_BYTES - 1}` },
    });
    const buf = Buffer.from(await upstream.arrayBuffer());
    const ct = upstream.headers.get("content-type") || "application/octet-stream";
    return new NextResponse(buf, {
      status: 200,
      headers: {
        "content-type": ct,
        "content-disposition": `inline; filename="stem-borer-proof"`,
        "x-upstream-status": String(upstream.status),
        "cache-control": "no-store",
      },
    });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || "Proof fetch failed." }, { status: 502 });
  } finally {
    clearTimeout(t);
  }
}
