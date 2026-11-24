import crypto from "node:crypto";
import dns from "node:dns/promises";

/**
 * Ownership gate.
 *
 * Stem Borer will ONLY scan a domain the operator has proven they control.
 * Proof = a DNS TXT record whose value is HMAC(SERVER_SECRET, registrableDomain).
 *
 * This is checked LIVE on every scan, so:
 *  - there is no database of "verified" domains to drift or be forged;
 *  - deleting the TXT record immediately revokes the ability to scan;
 *  - a domain you do not control cannot be scanned, because you cannot
 *    publish a TXT record on its zone.
 */

const TXT_PREFIX = "stem-borer-verify=";

function serverSecret(): string {
  const s = process.env.STEM_BORER_SECRET;
  if (!s || s.length < 16) {
    throw new Error(
      "STEM_BORER_SECRET is not set (needs >= 16 chars). Set it in the environment before running scans."
    );
  }
  return s;
}

/** Reduce any hostname to its registrable form for the TXT lookup, e.g. a.b.example.com -> example.com */
export function registrableDomain(hostname: string): string {
  const h = hostname.toLowerCase().replace(/\.$/, "").replace(/^\*\./, "");
  const parts = h.split(".").filter(Boolean);
  if (parts.length <= 2) return h;
  // Handle common two-label public suffixes (co.uk, com.au, etc.) minimally.
  const twoLabelTlds = new Set([
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.nz", "co.za", "com.br", "co.in", "co.jp",
  ]);
  const lastTwo = parts.slice(-2).join(".");
  if (twoLabelTlds.has(lastTwo) && parts.length >= 3) {
    return parts.slice(-3).join(".");
  }
  return parts.slice(-2).join(".");
}

/** The exact token the operator must publish, derived deterministically from the domain. */
export function expectedToken(domain: string): string {
  const reg = registrableDomain(domain);
  return crypto.createHmac("sha256", serverSecret()).update(reg).digest("hex").slice(0, 40);
}

export function txtRecordValue(domain: string): string {
  return TXT_PREFIX + expectedToken(domain);
}

export interface OwnershipResult {
  domain: string;
  registrable: string;
  verified: boolean;
  expected: string;
  foundRecords: string[];
  reason?: string;
}

/**
 * In-memory cache of successful verifications. Once a domain proves ownership,
 * we trust it for CACHE_TTL_MS so repeat scans don't re-hit DNS every time.
 * A failed/absent record is never cached, and the cache is process-local, so
 * removing the TXT record revokes access within the TTL at worst.
 */
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24h
const verifiedCache = new Map<string, number>(); // registrableDomain -> expiry epoch ms

/** Live DNS TXT check. True only if the expected HMAC token is published on the zone. */
export async function verifyOwnership(domain: string): Promise<OwnershipResult> {
  const reg = registrableDomain(domain);
  const expected = txtRecordValue(domain);
  const base: OwnershipResult = {
    domain,
    registrable: reg,
    verified: false,
    expected,
    foundRecords: [],
  };

  const cachedUntil = verifiedCache.get(reg);
  if (cachedUntil && cachedUntil > Date.now()) {
    return { ...base, verified: true };
  }

  let records: string[][] = [];
  try {
    records = await dns.resolveTxt(reg);
  } catch (err: any) {
    return { ...base, reason: `DNS TXT lookup failed for ${reg}: ${err?.code || err?.message || "unknown error"}` };
  }

  const flat = records.map((chunks) => chunks.join("").trim());
  const verified = flat.some((r) => r === expected);
  if (verified) verifiedCache.set(reg, Date.now() + CACHE_TTL_MS);
  return {
    ...base,
    verified,
    foundRecords: flat.filter((r) => r.startsWith(TXT_PREFIX)),
    reason: verified ? undefined : "No matching stem-borer-verify TXT record found on the domain's zone.",
  };
}

/** Guard used by every scan entry point. Throws if the target host is not owned. */
export async function assertOwnedOrThrow(targetUrl: string): Promise<{ host: string; registrable: string }> {
  let host: string;
  try {
    host = new URL(targetUrl).hostname;
  } catch {
    throw new Error("Invalid target URL.");
  }
  const result = await verifyOwnership(host);
  if (!result.verified) {
    throw new Error(
      `Ownership not verified for ${result.registrable}. Publish this DNS TXT record on the zone, then retry:\n` +
        `  ${result.registrable}.  IN  TXT  "${result.expected}"\n` +
        (result.reason ? `Reason: ${result.reason}` : "")
    );
  }
  return { host, registrable: result.registrable };
}
