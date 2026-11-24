"use client";

import { useEffect, useState } from "react";

type Severity = "critical" | "high" | "medium" | "low" | "info";

interface Finding {
  id: string;
  title: string;
  severity: Severity;
  category: string;
  summary: string;
  evidence: { url: string; method: string; status?: number; contentType?: string; bytes?: number; note?: string };
  proofUrl?: string;
  fix: string;
}

interface ScanReport {
  target: string;
  registrable: string;
  startedAt: string;
  finishedAt: string;
  mediaDiscovered: number;
  findings: Finding[];
  score: number;
}

const LS_KEY = "stem-borer:verified";
const CACHE_MS = 24 * 60 * 60 * 1000; // 24h

function loadCachedDomain(): string | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const { domain, ts } = JSON.parse(raw);
    if (domain && typeof ts === "number" && Date.now() - ts < CACHE_MS) return domain;
    localStorage.removeItem(LS_KEY);
  } catch {
    /* ignore */
  }
  return null;
}

function cacheDomain(domain: string) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ domain, ts: Date.now() }));
  } catch {
    /* ignore */
  }
}

export default function Page() {
  const [phase, setPhase] = useState<"verify" | "consent" | "scan">("verify");
  const [domain, setDomain] = useState("");
  const [record, setRecord] = useState<string>("");
  const [instructions, setInstructions] = useState<string>("");
  const [verifying, setVerifying] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState<string>("");
  const [verifyErr, setVerifyErr] = useState<string>("");
  const [consent, setConsent] = useState(false);

  const [target, setTarget] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanErr, setScanErr] = useState("");
  const [report, setReport] = useState<ScanReport | null>(null);

  useEffect(() => {
    const d = loadCachedDomain();
    if (d) {
      setDomain(d);
      setTarget("https://" + d);
      setPhase("scan"); // verified within 24h — skip straight to testing
    }
  }, []);

  async function getRecord() {
    setVerifyErr("");
    setVerifyMsg("");
    setRecord("");
    if (!domain.trim()) return setVerifyErr("Enter the domain you want to test (e.g. vibe.example).");
    setVerifying(true);
    try {
      const res = await fetch("/api/verify", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ domain }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Verification request failed.");
      setRecord(data.expectedRecord);
      setInstructions(data.instructions);
      if (data.verified) {
        cacheDomain(data.registrable);
        setVerifyMsg("Ownership confirmed. Continue below.");
        setPhase("consent");
      } else {
        setVerifyMsg("Record not found yet. Add it to your DNS, give it a minute, then click Verify.");
      }
    } catch (e: any) {
      setVerifyErr(e.message);
    } finally {
      setVerifying(false);
    }
  }

  async function verifyNow() {
    setVerifyErr("");
    setVerifying(true);
    try {
      const res = await fetch("/api/verify", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ domain }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Verification failed.");
      if (data.verified) {
        cacheDomain(data.registrable);
        setVerifyMsg("Ownership confirmed.");
        setPhase("consent");
      } else {
        setVerifyErr(data.reason || "Still not finding the TXT record. DNS can take a few minutes to propagate.");
      }
    } catch (e: any) {
      setVerifyErr(e.message);
    } finally {
      setVerifying(false);
    }
  }

  async function runScan() {
    setScanErr("");
    setReport(null);
    if (!target.trim()) return setScanErr("Enter a URL on your verified domain.");
    setScanning(true);
    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ target }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Scan failed.");
      setReport(data);
    } catch (e: any) {
      setScanErr(e.message);
    } finally {
      setScanning(false);
    }
  }

  async function downloadAllProof() {
    if (!report) return;
    const withProof = report.findings.filter((f) => f.proofUrl);
    for (const f of withProof) {
      const a = document.createElement("a");
      a.href = f.proofUrl!;
      a.download = `${f.id}-proof`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      await new Promise((r) => setTimeout(r, 400));
    }
  }

  function reset() {
    try {
      localStorage.removeItem(LS_KEY);
    } catch {
      /* ignore */
    }
    setPhase("verify");
    setReport(null);
    setRecord("");
    setVerifyMsg("");
    setVerifyErr("");
    setConsent(false);
  }

  const meterColor = (s: number) =>
    s >= 80 ? "var(--ok)" : s >= 50 ? "var(--med)" : "var(--crit)";

  return (
    <div className="wrap">
      <header className="hero">
        <div className="logo">SB</div>
        <div>
          <h1>Stem Borer</h1>
          <p className="sub">Find paywalled video &amp; image leaks on domains you own — and get the fix.</p>
        </div>
      </header>

      {phase === "verify" && (
        <div className="card">
          <div className="step"><span className="num">1</span> Verify you own the domain</div>
          <h2 style={{ marginTop: 10 }}>Which domain are you testing?</h2>
          <p className="hint">
            One-time setup. You prove the domain is yours once; after that you can test it as often as you like for 24h
            before it re-checks. Stem Borer refuses to scan anything you haven&apos;t verified.
          </p>
          <div className="row">
            <input
              type="text"
              placeholder="vibe.example"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && getRecord()}
            />
            <button onClick={getRecord} disabled={verifying}>
              {verifying ? <span className="spinner" /> : "Get verification record"}
            </button>
          </div>

          {record && (
            <>
              <div className="txt-record">{instructions}</div>
              <div className="row" style={{ marginTop: 12 }}>
                <button className="ghost" onClick={verifyNow} disabled={verifying}>
                  {verifying ? <span className="spinner" /> : "I've added it — Verify"}
                </button>
              </div>
            </>
          )}

          {verifyMsg && <p className="note">{verifyMsg}</p>}
          {verifyErr && <div className="error">{verifyErr}</div>}
        </div>
      )}

      {phase === "consent" && (
        <div className="card">
          <div className="step"><span className="num">2</span> Authorization</div>
          <h2 style={{ marginTop: 10 }}>Confirm you&apos;re authorized</h2>
          <p className="hint">
            <b>{domain}</b> is verified. Before testing, confirm you&apos;re authorized to run security tests against it.
          </p>
          <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14, cursor: "pointer" }}>
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} style={{ marginTop: 3 }} />
            <span>
              I own or am authorized to test <b>{domain}</b>, and I will use the results only to secure it. I understand
              Stem Borer only tests domains I have verified.
            </span>
          </label>
          <div className="row" style={{ marginTop: 16 }}>
            <button
              onClick={() => {
                setTarget("https://" + domain);
                setPhase("scan");
              }}
              disabled={!consent}
            >
              Continue to testing
            </button>
          </div>
        </div>
      )}

      {phase === "scan" && (
        <>
          <div className="card">
            <div className="step">
              <span className="num">3</span> Test a URL on {domain || "your verified domain"}
              <button className="ghost" style={{ marginLeft: "auto", padding: "6px 10px", fontSize: 12 }} onClick={reset}>
                Change domain
              </button>
            </div>
            <h2 style={{ marginTop: 10 }}>Paste the page or media URL to test</h2>
            <p className="hint">
              Must be on your verified domain. Stem Borer fetches it with no session and reports anything paid that
              answers anyway.
            </p>
            <div className="row">
              <input
                type="text"
                placeholder="https://vibe.example/watch/premium-123"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && runScan()}
              />
              <button onClick={runScan} disabled={scanning}>
                {scanning ? <span className="spinner" /> : "Run scan"}
              </button>
            </div>
            {scanErr && <div className="error">{scanErr}</div>}
          </div>

          {report && (
            <div className="card">
              <div className="score-wrap">
                <div className="score">
                  {report.score}
                  <small> / 100</small>
                </div>
                <div className="meter">
                  <span style={{ width: `${report.score}%`, background: meterColor(report.score) }} />
                </div>
              </div>
              <p className="note">
                {report.registrable} · {report.mediaDiscovered} media URL(s) discovered ·{" "}
                {report.findings.filter((f) => f.severity !== "info").length} issue(s) found
              </p>

              {report.findings.some((f) => f.proofUrl) && (
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="ghost" onClick={downloadAllProof}>
                    Download all proof ({report.findings.filter((f) => f.proofUrl).length})
                  </button>
                </div>
              )}

              {report.findings.map((f) => (
                <div className="finding" key={f.id}>
                  <div className="top">
                    <span className={`sev ${f.severity}`}>{f.severity}</span>
                    <span className="title">{f.title}</span>
                    <span className="id">{f.id}</span>
                  </div>
                  <p>{f.summary}</p>
                  <div className="evi">
                    {f.evidence.method} → {f.evidence.status ?? "no response"}
                    {f.evidence.contentType ? ` · ${f.evidence.contentType}` : ""}
                    {typeof f.evidence.bytes === "number" ? ` · ${f.evidence.bytes} bytes` : ""}
                    {"\n"}
                    {f.evidence.url}
                    {f.evidence.note ? `\n${f.evidence.note}` : ""}
                  </div>

                  {f.proofUrl && (
                    <div className="proof">
                      {/\b(image|img)\b/i.test(f.category) || /image/i.test(f.evidence.contentType || "") ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={f.proofUrl} alt="proof" />
                      ) : /video|manifest|media/i.test(f.category) || /video/i.test(f.evidence.contentType || "") ? (
                        <video src={f.proofUrl} controls />
                      ) : null}
                      <div className="row" style={{ marginTop: 8 }}>
                        <a href={f.proofUrl} download={`${f.id}-proof`}>
                          <button className="ghost">Download this proof</button>
                        </a>
                      </div>
                    </div>
                  )}

                  <div className="fix">
                    <b>Fix:</b> {f.fix}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <p className="foot">Stem Borer · tests only domains you&apos;ve verified · results are for securing your own platform</p>
    </div>
  );
}
