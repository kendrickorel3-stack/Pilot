"use client";

import { useState } from "react";

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

export default function Page() {
  const [target, setTarget] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanErr, setScanErr] = useState("");
  const [report, setReport] = useState<ScanReport | null>(null);

  async function runScan() {
    setScanErr("");
    setReport(null);
    if (!target.trim()) return setScanErr("Enter a page or media URL to test.");
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

  const meterColor = (s: number) =>
    s >= 80 ? "var(--ok)" : s >= 50 ? "var(--med)" : "var(--crit)";

  return (
    <div className="wrap">
      <header className="hero">
        <div className="logo">SB</div>
        <div>
          <h1>Stem Borer</h1>
          <p className="sub">Find paywalled video &amp; image leaks on your platform — and get the fix.</p>
        </div>
      </header>

      <div className="card">
        <div className="step">
          <span className="num">1</span> Paste a page or media URL to test
        </div>
        <h2 style={{ marginTop: 10 }}>What do you want to test?</h2>
        <p className="hint">
          Stem Borer fetches the URL with no session and reports anything paid that answers anyway. Targets must be in
          the scope you configured (<code>STEM_BORER_SCOPE</code>).
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

      <p className="foot">Stem Borer · tests only domains in your configured scope · results are for securing your own platform</p>
    </div>
  );
}
