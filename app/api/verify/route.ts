import { NextRequest, NextResponse } from "next/server";
import { verifyOwnership, txtRecordValue, registrableDomain } from "@/lib/ownership";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let domain = "";
  try {
    const body = await req.json();
    domain = String(body.domain || "").trim();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }
  if (!domain) return NextResponse.json({ error: "Provide a domain." }, { status: 400 });

  // Accept a full URL or a bare host.
  try {
    if (/^https?:\/\//i.test(domain)) domain = new URL(domain).hostname;
  } catch {
    /* fall through */
  }

  let reg = "";
  let expected = "";
  try {
    reg = registrableDomain(domain);
    expected = txtRecordValue(domain);
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }

  const result = await verifyOwnership(domain);
  return NextResponse.json({
    domain,
    registrable: reg,
    verified: result.verified,
    expectedRecord: expected,
    instructions: `Add this TXT record to ${reg}, then click Verify:\n  Host: @ (or ${reg})\n  Type: TXT\n  Value: ${expected}`,
    foundRecords: result.foundRecords,
    reason: result.reason,
  });
}
