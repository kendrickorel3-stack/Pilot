import { NextRequest, NextResponse } from "next/server";
import { assertInScopeOrThrow } from "@/lib/scope";
import { MAX_PROOF_BYTES, UA } from "@/lib/scanner";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

/**
 * Captures a media URL as PROOF that it was served to an UNAUTHENTICATED request
 * — the full asset (up to MAX_PROOF_BYTES), so the operator has real evidence of
 * the leak, not a truncated sample.
 *
 * This only ever pulls what the origin already hands out with no session: the
 * same unauthenticated GET the scanner used to flag the leak. It sends no
 * cookies, tokens, or auth headers, and does nothing to defeat a working gate —
 * if the origin requires auth, it returns auth's response, not the content.
 *
 * Re-checks scope here too: the media host must reduce to a domain in the
 * operator's declared STEM_BORER_SCOPE. This endpoint cannot be used to pull
 * content from a domain outside that authorized scope.
 */
export async function GET(req: NextRequest) {
  const url = req.nextUrl.searchParams.get("url") || "";
  if (!url || !/^https?:\/\//i.test(url)) {
    return NextResponse.json({ error: "Missing or invalid url." }, { status: 400 });
  }

  try {
    assertInScopeOrThrow(url);
  } catch (e: any) {
    return NextResponse.json(
      { error: `Proof refused: ${e.message}`, code: "OUT_OF_SCOPE" },
      { status: 403 }
    );
  }

  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), 45000);
  try {
    // Plain unauthenticated GET — no range, no cookies, no auth. Whatever the
    // origin serves to anyone is what we capture.
    const upstream = await fetch(url, {
      signal: controller.signal,
      redirect: "follow",
      headers: { "user-agent": UA },
    });

    // If the gate actually works, surface that instead of pretending it's proof.
    if (upstream.status !== 200 && upstream.status !== 206) {
      return NextResponse.json(
        {
          error: `No leak to capture: the origin returned ${upstream.status} to an unauthenticated request. The content is gated as intended.`,
          code: "NOT_LEAKED",
          upstreamStatus: upstream.status,
        },
        { status: 409 }
      );
    }

    // Read the full body, but stop at the cap so a huge file can't exhaust memory.
    const reader = upstream.body?.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    let truncated = false;
    if (reader) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) {
          if (total + value.byteLength > MAX_PROOF_BYTES) {
            chunks.push(value.subarray(0, MAX_PROOF_BYTES - total));
            total = MAX_PROOF_BYTES;
            truncated = true;
            await reader.cancel().catch(() => {});
            break;
          }
          chunks.push(value);
          total += value.byteLength;
        }
      }
    }

    const buf = Buffer.concat(chunks.map((c) => Buffer.from(c)));
    const ct = upstream.headers.get("content-type") || "application/octet-stream";
    return new NextResponse(buf, {
      status: 200,
      headers: {
        "content-type": ct,
        "content-disposition": `inline; filename="stem-borer-proof"`,
        "x-upstream-status": String(upstream.status),
        "x-proof-bytes": String(total),
        "x-proof-truncated": String(truncated),
        "cache-control": "no-store",
      },
    });
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || "Proof fetch failed." }, { status: 502 });
  } finally {
    clearTimeout(t);
  }
}
