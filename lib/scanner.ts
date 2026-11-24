/**
 * Stem Borer scanner engine.
 *
 * Every test here runs ONLY after lib/scope.assertInScopeOrThrow has passed,
 * so all requests go to a domain in the operator's declared scope. The goal is to find
 * paywalled video/image content that is reachable WITHOUT a paid session, and
 * to tell the operator exactly how to fix each hole.
 */

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export interface Evidence {
  url: string;
  method: string;
  status?: number;
  contentType?: string;
  bytes?: number;
  note?: string;
}

export interface Finding {
  id: string;
  title: string;
  severity: Severity;
  category: "direct-media" | "signed-url" | "manifest" | "client-gate" | "discovery";
  summary: string;
  evidence: Evidence;
  /** Media that was reachable unauthenticated; the UI offers to preview/download it as proof. */
  proofUrl?: string;
  fix: string;
}

export interface ScanReport {
  target: string;
  registrable: string;
  startedAt: string;
  finishedAt: string;
  mediaDiscovered: number;
  findings: Finding[];
  score: number; // 0 (wide open) .. 100 (no issues found)
}

const UA = "StemBorer/1.0 (+security-self-test; authorized-scope)";
const FETCH_TIMEOUT_MS = 12000;
const SAMPLE_BYTES = 262144; // 256 KB probe sample for reachability detection
const MAX_PROOF_BYTES = 50 * 1024 * 1024; // 50 MB cap on full-evidence capture

/**
 * Public-by-design assets. These are MEANT to be reachable without a session —
 * flagging them as "leaked gated media" is a false positive. Matched on the URL
 * path so branding/chrome never shows up as a finding.
 */
const PUBLIC_ASSET =
  /(favicon|apple-touch-icon|android-chrome|mstile|safari-pinned-tab|\/icons?\/|[-_/](logo|brand|sprite|placeholder|avatar|og[-_]?image|twitter[-_]?(image|card))[-_.]?)/i;

function isPublicAsset(url: string): boolean {
  try {
    return PUBLIC_ASSET.test(new URL(url).pathname);
  } catch {
    return PUBLIC_ASSET.test(url);
  }
}

function timeoutSignal(ms: number) {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  return { signal: c.signal, clear: () => clearTimeout(t) };
}

async function safeFetch(url: string, init: RequestInit = {}): Promise<Response | null> {
  const { signal, clear } = timeoutSignal(FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...init,
      signal,
      redirect: "follow",
      headers: { "user-agent": UA, ...(init.headers || {}) },
    });
  } catch {
    return null;
  } finally {
    clear();
  }
}

const MEDIA_EXT = /\.(mp4|m4v|mov|webm|m3u8|mpd|ts|mp3|m4a|jpg|jpeg|png|gif|webp|avif)(\?|#|$)/i;

function absolutize(base: string, ref: string): string | null {
  try {
    return new URL(ref, base).toString();
  } catch {
    return null;
  }
}

/** Pull candidate media URLs out of a page's HTML without executing JS. */
function extractMedia(baseUrl: string, html: string): string[] {
  const urls = new Set<string>();
  const add = (ref?: string | null) => {
    if (!ref) return;
    const abs = absolutize(baseUrl, ref.trim());
    if (abs && MEDIA_EXT.test(abs)) urls.add(abs);
  };

  const attrRe = /(?:src|href|data-src|data-video|poster|content)\s*=\s*["']([^"']+)["']/gi;
  let m: RegExpExecArray | null;
  while ((m = attrRe.exec(html))) add(m[1]);

  // srcset
  const srcsetRe = /srcset\s*=\s*["']([^"']+)["']/gi;
  while ((m = srcsetRe.exec(html))) {
    for (const part of m[1].split(",")) add(part.trim().split(/\s+/)[0]);
  }

  // bare URLs inside scripts/JSON (common for players)
  const bareRe = /https?:\/\/[^\s"'<>\\)]+/gi;
  while ((m = bareRe.exec(html))) add(m[0]);

  return Array.from(urls).slice(0, 40);
}

const PAYWALL_MARKERS = /(paywall|subscribe|premium|members?-only|locked|unlock|upgrade|sign\s*in\s*to\s*watch)/i;

/** Params that usually carry access signing. */
const SIGNING_PARAMS = /(token|sig|signature|expires?|policy|key|hmac|auth|jwt|st|e)$/i;

function stripSigningParams(url: string): string | null {
  try {
    const u = new URL(url);
    let changed = false;
    for (const k of Array.from(u.searchParams.keys())) {
      if (SIGNING_PARAMS.test(k)) {
        u.searchParams.delete(k);
        changed = true;
      }
    }
    return changed ? u.toString() : null;
  } catch {
    return null;
  }
}

async function probe(url: string): Promise<{ status?: number; contentType?: string; bytes?: number }> {
  // Ranged GET so we don't pull whole files — reachability is the finding.
  const res = await safeFetch(url, { headers: { range: `bytes=0-${SAMPLE_BYTES - 1}` } });
  if (!res) return {};
  const status = res.status;
  const contentType = res.headers.get("content-type") || undefined;
  let bytes: number | undefined;
  const len = res.headers.get("content-length");
  if (len) bytes = parseInt(len, 10);
  try {
    const buf = await res.arrayBuffer();
    if (bytes === undefined) bytes = buf.byteLength;
  } catch {
    /* ignore body read errors */
  }
  return { status, contentType, bytes };
}

function isReachable(status?: number): boolean {
  return status === 200 || status === 206;
}

function proofLink(owned: string, mediaUrl: string): string {
  const q = new URLSearchParams({ domain: owned, url: mediaUrl });
  return `/api/proof?${q.toString()}`;
}

export async function runScan(target: string, registrable: string): Promise<ScanReport> {
  const startedAt = new Date().toISOString();
  const findings: Finding[] = [];
  let idN = 0;
  const nextId = () => `SB-${String(++idN).padStart(3, "0")}`;

  // 1. Fetch the page unauthenticated (no cookies, no session).
  const pageRes = await safeFetch(target);
  const html = pageRes && isReachable(pageRes.status) ? await pageRes.text().catch(() => "") : "";
  const pageLocked = PAYWALL_MARKERS.test(html);

  const media = extractMedia(target, html);

  // 2. Probe each discovered media URL without auth.
  let publicSkipped = 0;
  for (const mu of media) {
    // Skip site chrome / branding — public by design, not a paywall leak.
    if (isPublicAsset(mu)) {
      publicSkipped++;
      continue;
    }

    const r = await probe(mu);
    if (!isReachable(r.status)) continue;

    const isImage = /image\//i.test(r.contentType || "") || /\.(jpe?g|png|gif|webp|avif)(\?|#|$)/i.test(mu);
    const isManifest = /\.(m3u8|mpd)(\?|#|$)/i.test(mu);
    const severity: Severity = isManifest ? "critical" : isImage ? "medium" : "high";

    findings.push({
      id: nextId(),
      title: isManifest
        ? "Streaming manifest reachable without authentication"
        : isImage
        ? "Gated image reachable without authentication"
        : "Gated video reachable without authentication",
      severity,
      category: isManifest ? "manifest" : "direct-media",
      summary: `The media at this URL returned ${r.status} to an unauthenticated request. If it is paid content, anyone with the URL can access it.`,
      evidence: { url: mu, method: "GET (range 0-256KB, no session cookie)", status: r.status, contentType: r.contentType, bytes: r.bytes },
      proofUrl: proofLink(registrable, mu),
      fix: isManifest
        ? "Serve HLS/DASH manifests and segments through an authenticated endpoint or short-lived signed URLs tied to the user's entitlement. Do not expose the manifest on a public/static path."
        : "Put this file behind a server-side access check (verify the session + active entitlement before returning bytes), or front it with per-user short-lived signed URLs. Remove any public/static route to the raw file.",
    });

    // 3. Signed-URL tampering: does stripping the token still work?
    const stripped = stripSigningParams(mu);
    if (stripped) {
      const r2 = await probe(stripped);
      if (isReachable(r2.status)) {
        findings.push({
          id: nextId(),
          title: "Access token is decorative — content loads without it",
          severity: "critical",
          category: "signed-url",
          summary: "Removing the signing/expiry parameters still returned the content. The signature is not being enforced server-side.",
          evidence: { url: stripped, method: "GET without signing params", status: r2.status, contentType: r2.contentType, bytes: r2.bytes, note: `original: ${mu}` },
          proofUrl: proofLink(registrable, stripped),
          fix: "Enforce the signature on the server: reject requests whose token is missing, altered, or expired. A CDN that passes unsigned requests through to origin is the usual culprit — require signed URLs at the edge.",
        });
      }
    }

    // 4. Manifest expansion: test the first segment too.
    if (isManifest && /\.m3u8(\?|#|$)/i.test(mu)) {
      const manRes = await safeFetch(mu);
      const manText = manRes && isReachable(manRes.status) ? await manRes.text().catch(() => "") : "";
      const seg = manText
        .split(/\r?\n/)
        .map((l) => l.trim())
        .find((l) => l && !l.startsWith("#"));
      if (seg) {
        const segUrl = absolutize(mu, seg);
        if (segUrl) {
          const rs = await probe(segUrl);
          if (isReachable(rs.status)) {
            findings.push({
              id: nextId(),
              title: "Video segments downloadable without authentication",
              severity: "critical",
              category: "manifest",
              summary: "A media segment referenced by the manifest was fetched unauthenticated. The full stream can be reconstructed.",
              evidence: { url: segUrl, method: "GET segment, no session", status: rs.status, contentType: rs.contentType, bytes: rs.bytes },
              proofUrl: proofLink(registrable, segUrl),
              fix: "Authenticate every segment request, not just the manifest. Use signed segment URLs or an authorizing proxy; never serve .ts/.m4s segments from a public bucket.",
            });
          }
        }
      }
    }
  }

  // 5. Cosmetic gate: page says 'locked' but media was reachable anyway.
  const anyReachable = findings.some((f) => f.category === "direct-media" || f.category === "manifest");
  if (pageLocked && anyReachable) {
    findings.push({
      id: nextId(),
      title: "Paywall is client-side only (cosmetic lock)",
      severity: "high",
      category: "client-gate",
      summary: "The page shows paywall/subscribe UI, but the underlying media was served to an unauthenticated request. The lock is in the frontend, not on the server.",
      evidence: { url: target, method: "GET page, no session", note: "paywall markers present in HTML while media was directly reachable" },
      fix: "Move the gate to the server. The frontend hiding a player does nothing if the media URL answers to anyone. Enforce entitlement where the bytes are served.",
    });
  }

  const realMedia = media.length - publicSkipped;
  if (realMedia <= 0) {
    findings.push({
      id: nextId(),
      title: "No gated media URLs found in page HTML",
      severity: "info",
      category: "discovery",
      summary:
        publicSkipped > 0
          ? `Only public assets (favicons/branding) were found in the HTML — nothing that looks like gated media. The player may inject URLs via JavaScript/API — point Stem Borer at the API/manifest URL directly to test those.`
          : "No media was discoverable from the server-rendered HTML. The player may inject URLs via JavaScript/API — point Stem Borer at the API/manifest URL directly to test those.",
      evidence: { url: target, method: "GET page", status: pageRes?.status },
      fix: "If media loads via an API call, run a scan against that API/manifest endpoint so Stem Borer can test it directly.",
    });
  }

  // Score: start at 100, subtract weighted by severity of real (non-info) findings.
  const weight: Record<Severity, number> = { critical: 34, high: 20, medium: 10, low: 4, info: 0 };
  const penalty = findings.reduce((s, f) => s + weight[f.severity], 0);
  const score = Math.max(0, 100 - penalty);

  return {
    target,
    registrable,
    startedAt,
    finishedAt: new Date().toISOString(),
    mediaDiscovered: Math.max(0, media.length - publicSkipped),
    findings,
    score,
  };
}

export { SAMPLE_BYTES, MAX_PROOF_BYTES, UA };
