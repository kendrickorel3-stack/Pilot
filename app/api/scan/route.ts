import { NextRequest, NextResponse } from "next/server";
import { assertOwnedOrThrow } from "@/lib/ownership";
import { runScan } from "@/lib/scanner";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(req: NextRequest) {
  let target = "";
  try {
    const body = await req.json();
    target = String(body.target || "").trim();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }
  if (!target) return NextResponse.json({ error: "Provide a target URL." }, { status: 400 });
  if (!/^https?:\/\//i.test(target)) target = "https://" + target;

  // OWNERSHIP GATE — throws unless the target domain has the DNS TXT proof.
  let owned: { host: string; registrable: string };
  try {
    owned = await assertOwnedOrThrow(target);
  } catch (e: any) {
    return NextResponse.json({ error: e.message, code: "OWNERSHIP_REQUIRED" }, { status: 403 });
  }

  try {
    const report = await runScan(target, owned.registrable);
    return NextResponse.json(report);
  } catch (e: any) {
    return NextResponse.json({ error: e?.message || "Scan failed." }, { status: 500 });
  }
}
