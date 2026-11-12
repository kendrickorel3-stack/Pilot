"""Self-contained HTML report per run, so a bookkeeper can audit every step the agent took."""
from __future__ import annotations

import html
import json
from pathlib import Path

STATUS_COLOR = {"success": "#2f7d4a", "partial": "#b7791f", "failed": "#b4382c"}


def write_report(run_dir: Path, portal, result, events: list[dict], tokens: dict) -> Path:
    esc = html.escape
    rows = []
    for e in events:
        k = e["kind"]
        if k == "model":
            what = "; ".join(f"{c['name']}({_fmt(c['input'])})" for c in e.get("calls", [])) or "no action"
            note = " ".join(e.get("notes", []))
            rows.append(f"<tr><td>{e.get('step', '')}</td><td>Agent</td><td>{esc(what)}"
                        f"{'<div class=n>' + esc(note[:400]) + '</div>' if note else ''}</td><td></td></tr>")
        elif k == "tool":
            ok = "ok" if e.get("ok") else "blocked / failed"
            detail = esc(e.get("error") or e.get("url") or "")
            shot = f'<a href="shots/{esc(e["shot"])}">screenshot</a>' if e.get("shot") else ""
            rows.append(f"<tr class={'ok' if e.get('ok') else 'bad'}><td>{e.get('step', '')}</td><td>{esc(e['name'])}: {ok}</td>"
                        f"<td>{detail}</td><td>{shot}</td></tr>")
        elif k == "download":
            rows.append(f"<tr class=ok><td></td><td>Downloaded</td><td>{esc(e['label'])} &rarr; {esc(Path(e['file']).name)}"
                        f"{' (duplicate, skipped)' if e.get('duplicate') else ''}</td><td></td></tr>")
        elif k == "human":
            rows.append(f"<tr><td></td><td>Asked bookkeeper</td><td>{esc(e['question'])}</td><td></td></tr>")
        elif k == "api_error":
            rows.append(f"<tr class=bad><td></td><td>API error</td><td>{esc(e['error'])}</td><td></td></tr>")
    files = "".join(f"<li>{esc(d['label'])} &mdash; <code>{esc(Path(d['file']).name)}</code></li>" for d in result.downloads) or "<li>None</li>"
    color = STATUS_COLOR.get(result.status, "#555")
    page = f"""<!doctype html><meta charset=utf-8><title>Run report: {esc(portal.name)}</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1d2420;background:#fbfbf8}}
h1{{font-size:22px;margin:0 0 4px}} .s{{display:inline-block;padding:2px 10px;border-radius:12px;color:#fff;background:{color};font-weight:600}}
table{{border-collapse:collapse;width:100%;margin-top:12px}} td{{border-bottom:1px solid #e3e3dc;padding:6px 8px;vertical-align:top}}
tr.bad td{{background:#fdf1ef}} .n{{color:#5c665f;font-size:12px;margin-top:2px}} code{{font-size:12px}}
.meta{{color:#5c665f}}
</style>
<h1>{esc(portal.client)} &middot; {esc(portal.name)}</h1>
<p class=meta>{esc(portal.task())}</p>
<p><span class=s>{esc(result.status)}</span> &nbsp;{result.steps} steps &middot; model cost ${result.cost_usd:.3f}
&middot; tokens {json.dumps(tokens)}</p>
<p>{esc(result.summary)}</p>
<h2>Files</h2><ul>{files}</ul>
<h2>Every step</h2><table>{''.join(rows)}</table>"""
    path = run_dir / "report.html"
    path.write_text(page)
    return path


def _fmt(d: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in (d or {}).items())
