/**
 * Authorized scope.
 *
 * Stem Borer tests paywall/media access-control on OTHER people's servers, so it
 * only ever fires at domains the operator has declared they are cleared to test.
 * There is no per-scan verification dance: you set your scope ONCE in the
 * environment and every scan after that is instant.
 *
 *   STEM_BORER_SCOPE="vibe.example, steez.example, mine.co.uk"
 *
 * A target is allowed when its registrable domain is in that list. The list is
 * required and fails closed: if it is empty, nothing scans. This keeps the tool
 * frictionless to use while still refusing to point at assets you haven't
 * declared authorization for.
 */

/** Reduce any hostname to its registrable form, e.g. a.b.example.com -> example.com */
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

/** The operator's declared, authorized scope — registrable domains, parsed once from the env. */
export function allowedScope(): string[] {
  return (process.env.STEM_BORER_SCOPE || "")
    .split(/[,\s]+/)
    .map((d) => d.trim().toLowerCase())
    .filter(Boolean)
    .map((d) => registrableDomain(d));
}

export function isInScope(hostname: string): boolean {
  const reg = registrableDomain(hostname);
  return allowedScope().includes(reg);
}

/** Guard used by every scan/proof entry point. Throws unless the target is in declared scope. */
export function assertInScopeOrThrow(targetUrl: string): { host: string; registrable: string } {
  let host: string;
  try {
    host = new URL(targetUrl).hostname;
  } catch {
    throw new Error("Invalid target URL.");
  }
  const reg = registrableDomain(host);
  const scope = allowedScope();

  if (scope.length === 0) {
    throw new Error(
      "No authorized scope is configured. Set STEM_BORER_SCOPE to the domain(s) you're cleared to test " +
        '(e.g. STEM_BORER_SCOPE="vibe.example") and restart.'
    );
  }
  if (!scope.includes(reg)) {
    throw new Error(
      `${reg} is not in your authorized scope. Stem Borer only tests domains listed in STEM_BORER_SCOPE ` +
        `(currently: ${scope.join(", ")}). Add it there if you're cleared to test it.`
    );
  }
  return { host, registrable: reg };
}
