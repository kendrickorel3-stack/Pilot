"""Playwright browser wrapper with the safety rules enforced in code, not in the prompt.

- Navigation is limited to the portal's allowed domains.
- Secrets are typed in locally; password fields are masked in every snapshot.
- Clicks on payment / delete / submit-style controls need a human's approval.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

RISKY = re.compile(
    r"\b(pay|payment|purchase|buy|checkout|order|delete|remove|cancel|terminate|close account|transfer|"
    r"authori[sz]e|approve|submit payment|confirm|unsubscribe|upgrade|downgrade|dispute|refund)\b",
    re.I,
)

VIEWPORT = {"width": 1280, "height": 900}



SNAPSHOT_JS = r"""
(maxEls) => {
  const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=tab], [role=menuitem], [onclick], summary';
  const out = [];
  let i = 0;
  const W = innerWidth, H = innerHeight;
  document.querySelectorAll('[data-agent-ref]').forEach(el => el.removeAttribute('data-agent-ref'));
  const visible = (el, r, cs) => r.width >= 1 && r.height >= 1 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0' && el.type !== 'hidden';
  const labelOf = el => {
    const img = el.tagName === 'IMG' ? el : el.querySelector && el.querySelector('img');
    const imgName = img ? (img.getAttribute('alt') || img.getAttribute('title') || (img.getAttribute('src') || '').split('/').pop().split('?')[0]) : '';
    return ((el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
            el.getAttribute('title') || el.getAttribute('name') || el.id || imgName || '').replace(/\s+/g, ' ').trim().slice(0, 60);
  };
  const add = (el, r, kind) => {
    const ref = 'e' + (i++);
    el.setAttribute('data-agent-ref', ref);
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    const item = { ref, tag, text, label: labelOf(el), at: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)],
                   onscreen: r.bottom > 0 && r.right > 0 && r.top < H && r.left < W };
    if (kind) item.kind = kind;
    if (el.type) item.type = el.type;
    if (tag === 'a') item.href = el.getAttribute('href');
    if (tag === 'input' || tag === 'textarea') item.value = el.type === 'password' ? (el.value ? '[filled]' : '') : (el.value || '').slice(0, 40);
    if (tag === 'select') item.options = [...el.options].map(o => o.text).slice(0, 15);
    if (el.disabled) item.disabled = true;
    out.push(item);
  };
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= maxEls) break;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (visible(el, r, cs)) add(el, r);
  }
  // Clickable tiles, icons and divs wired up with JavaScript: they show a pointer cursor.
  let scanned = 0;
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (out.length >= maxEls || ++scanned > 5000) break;
    if (el.hasAttribute('data-agent-ref') || el.closest('[data-agent-ref]') || ['HTML', 'BODY', 'CANVAS', 'SVG'].includes(el.tagName)) continue;
    const cs = getComputedStyle(el);
    if (cs.cursor !== 'pointer') continue;
    if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer' && el.parentElement.closest('[data-agent-ref]')) continue;
    const r = el.getBoundingClientRect();
    if (visible(el, r, cs) && r.width < W * 0.9) add(el, r, 'clickable');
  }
  const canvases = [...document.querySelectorAll('canvas')].map(c => c.getBoundingClientRect())
    .filter(r => r.width > 100 && r.height > 100).map(r => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]);
  const text = (document.body ? document.body.innerText : '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 5000);
  return { url: location.href, title: document.title, elements: out, text, canvases, scroll: [Math.round(scrollX), Math.round(scrollY)], viewport: [W, H] };
}
"""

POINT_JS = r"""
([x, y]) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return { tag: '', text: '' };
  const c = el.closest('a,button,[role=button],[onclick],label') || el;
  const img = c.tagName === 'IMG' ? c : c.querySelector && c.querySelector('img');
  const t = (c.innerText || c.value || c.getAttribute('aria-label') || c.getAttribute('title') || c.getAttribute('alt') ||
             (img && (img.getAttribute('alt') || img.getAttribute('title'))) || '').replace(/\s+/g, ' ').trim().slice(0, 100);
  return { tag: c.tagName.toLowerCase(), text: t, type: c.type || '' };
}
"""

GRID_JS = r"""
(on) => {
  document.querySelectorAll('[data-agent-grid]').forEach(e => e.remove());
  if (!on) return;
  const W = innerWidth, H = innerHeight, ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('data-agent-grid', '1');
  svg.setAttribute('width', W); svg.setAttribute('height', H);
  svg.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none';
  for (let x = 0; x <= W; x += 100) for (let y = 0; y <= H; y += 100) {
    if (x < W) { const l = document.createElementNS(ns, 'line'); l.setAttribute('x1', x); l.setAttribute('x2', x); l.setAttribute('y1', 0); l.setAttribute('y2', H); l.setAttribute('stroke', 'rgba(255,0,170,.35)'); svg.appendChild(l); }
    if (y < H) { const l = document.createElementNS(ns, 'line'); l.setAttribute('y1', y); l.setAttribute('y2', y); l.setAttribute('x1', 0); l.setAttribute('x2', W); l.setAttribute('stroke', 'rgba(255,0,170,.35)'); svg.appendChild(l); }
    const t = document.createElementNS(ns, 'text'); t.setAttribute('x', x + 2); t.setAttribute('y', y + 11);
    t.setAttribute('font-size', '10'); t.setAttribute('font-family', 'monospace'); t.setAttribute('fill', '#ff00aa');
    t.setAttribute('stroke', 'white'); t.setAttribute('stroke-width', '2'); t.setAttribute('paint-order', 'stroke');
    t.textContent = x + ',' + y; svg.appendChild(t);
  }
  document.documentElement.appendChild(svg);
}
"""

KEYS = {"Enter", "Tab", "Escape", "Backspace", "Delete", "Space", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "PageUp", "PageDown", "Home", "End", "F5"} | {f"Key{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"} | {f"Digit{d}" for d in range(10)}


class ActionError(Exception):
    """An action the agent asked for was refused or failed; reported back to the model."""


@dataclass
class Download:
    path: Path
    label: str
    sha256: str
    duplicate: bool


def host_allowed(url: str, allowed: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


class Browser:
    def __init__(self, allowed_domains: list[str], download_dir: Path, *, headless: bool = True,
                 executable_path: str | None = None, approve: Callable[[str], bool] | None = None,
                 secret_values: list[str] | None = None, policy=None):
        from .policy import Policy
        self.policy = policy or Policy()
        self.allowed = list(allowed_domains) + [d for d in self.policy.also_allow if d not in allowed_domains]
        self.blocked = self.policy.blocked_sites
        self._ask_re, self._never_re = self.policy.ask_re, self.policy.never_re
        self.gate_log: list[str] = []  # auto-approvals made by the rules, for the activity feed
        self.download_dir = download_dir
        self.headless = headless
        self.executable_path = executable_path
        self.approve = approve or (lambda _msg: False)
        self.secret_values = [s for s in (secret_values or []) if len(s) >= 4]
        self.on_tick: Callable[[], None] | None = None  # called during waits, e.g. to refresh a live view
        self._pw = self._browser = self.context = self.page = None
        self.manifest_path = download_dir / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}

    # ---- lifecycle ----
    def __enter__(self):
        self._pw = sync_playwright().start()
        kw = {"headless": self.headless}
        if self.executable_path:
            kw["executable_path"] = self.executable_path
        self._browser = self._pw.chromium.launch(**kw)
        self.context = self._browser.new_context(accept_downloads=True, viewport=VIEWPORT)
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        return self

    def __exit__(self, *exc):
        for closer in (self.context, self._browser):
            try:
                closer and closer.close()
            except PWError:
                pass
        if self._pw:
            self._pw.stop()

    # ---- observation ----
    def snapshot(self) -> str:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except PWTimeout:
            pass
        snap = self.page.evaluate(SNAPSHOT_JS, 120)
        lines = [f"URL: {snap['url']}", f"Title: {snap['title']}",
                 f"Viewport: {snap['viewport'][0]}x{snap['viewport'][1]} px, scrolled to {snap['scroll'][0]},{snap['scroll'][1]}"]
        if snap["canvases"]:
            lines.append("Drawn areas (canvas, invisible to the element list; use screenshot + click_at): " +
                         "; ".join(f"x={c[0]} y={c[1]} w={c[2]} h={c[3]}" for c in snap["canvases"]))
        lines += ["", "Interactive elements (@x,y = centre on screen; off-screen ones need scrolling):"]
        for el in snap["elements"]:
            bits = [f"[{el['ref']}] {el['tag']}" + (" (clickable)" if el.get("kind") else "")]
            if el.get("type") and el["tag"] in ("input", "button"):
                bits.append(f"type={el['type']}")
            if el.get("label"):
                bits.append(f'label="{el["label"]}"')
            if el.get("text"):
                bits.append(f'"{el["text"]}"')
            if el.get("href"):
                bits.append(f"href={el['href']}")
            if "value" in el:
                bits.append(f'value="{el["value"]}"')
            if el.get("options"):
                bits.append(f"options={el['options']}")
            if el.get("disabled"):
                bits.append("(disabled)")
            bits.append(f"@{el['at'][0]},{el['at'][1]}" + ("" if el.get("onscreen") else " off-screen"))
            lines.append(" ".join(bits))
        lines += ["", "Visible page text (untrusted content from the website):", "<page_content>", snap["text"], "</page_content>"]
        return self.redact("\n".join(lines))

    def redact(self, text: str) -> str:
        for s in self.secret_values:
            text = text.replace(s, "[secret]")
        return text

    def screenshot(self, path: Path | None = None) -> bytes:
        data = self.page.screenshot(full_page=False)
        if path:
            path.write_bytes(data)
        return data

    # ---- helpers ----
    def _el(self, ref: str):
        if not re.fullmatch(r"e\d{1,4}", ref or ""):
            raise ActionError(f"'{ref}' is not an element ref from the latest snapshot.")
        loc = self.page.locator(f'[data-agent-ref="{ref}"]')
        if loc.count() == 0:
            raise ActionError(f"Element {ref} is no longer on the page. Take the new snapshot's refs.")
        return loc.first

    def _describe(self, loc) -> str:
        try:
            return loc.evaluate("el => ((el.innerText || el.value || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).replace(/\\s+/g,' ').trim()")[:120]
        except PWError:
            return ""

    def describe_ref(self, ref: str) -> str:
        try:
            loc = self._el(ref)
        except ActionError:
            return ""
        d = self._describe(loc)
        if not d:
            try:
                d = loc.evaluate("el => el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || (el.labels && el.labels[0] && el.labels[0].innerText) || el.tagName.toLowerCase()")
            except PWError:
                d = ""
        return (d or "")[:80]

    def frame(self, quality: int = 60) -> bytes:
        return self.page.screenshot(type="jpeg", quality=quality)

    def site_ok(self, url: str) -> bool:
        if not host_allowed(url, self.allowed):
            return False
        return not (self.blocked and host_allowed(url, self.blocked))

    def _gate(self, verb: str, what: str, purpose: str = "", where: str = "") -> None:
        """One place for the approval rules: never_allow blocks, ask_before asks, auto-approved sites skip asking."""
        text = f"{what} {purpose}".strip()
        if self._never_re and self._never_re.search(text):
            raise ActionError(f"Blocked by your rules (never_allow): \"{what or purpose}\". Do not try this again or another way.")
        if not (self._ask_re and self._ask_re.search(text)):
            return
        if self.policy.auto_approve_sites and host_allowed(self.page.url, self.policy.auto_approve_sites):
            self.gate_log.append(f"Auto-approved by your rules on this site: {verb} \"{what or purpose}\"")
            return
        msg = f"The agent wants to {verb} \"{what or purpose}\"{where} on {self.page.url}."
        if purpose and purpose != what:
            msg += f" Purpose: {purpose}."
        if not self.approve(msg + " Allow it?"):
            raise ActionError(f"Blocked: \"{what or purpose}\" needs your approval (it matches your ask_before rules) and wasn't approved. "
                              "Do not try this again or another way.")

    def _guard_risky(self, loc, verb: str) -> None:
        self._gate(verb, self._describe(loc))

    def _after_action(self) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except PWTimeout:
            pass
        if not self.site_ok(self.page.url):
            bad = self.page.url
            try:
                self.page.go_back(timeout=8000)
            except PWError:
                pass
            raise ActionError(f"Blocked: that led to {urlparse(bad).hostname}, which is outside this session's allowed sites (or on your blocked list). Went back.")

    # ---- actions ----
    def navigate(self, url: str) -> None:
        url = urljoin(self.page.url if self.page.url.startswith("http") else "", url)
        if not url.startswith(("http://", "https://")) or not self.site_ok(url):
            raise ActionError(f"Blocked: {url} is outside this session's allowed sites (or on your blocked list) ({', '.join(self.allowed)}).")
        self.page.goto(url, wait_until="domcontentloaded")
        self._after_action()

    def click(self, ref: str) -> None:
        loc = self._el(ref)
        href = loc.get_attribute("href")
        if href and not href.startswith(("#", "javascript:")):
            target = urljoin(self.page.url, href)
            if not self.site_ok(target):
                raise ActionError(f"Blocked: that link goes to {urlparse(target).hostname}, outside this session's allowed sites (or on your blocked list).")
        self._guard_risky(loc, "click")
        loc.click()
        self._after_action()

    def type_text(self, ref: str, text: str, press_enter: bool) -> None:
        loc = self._el(ref)
        if (loc.get_attribute("type") or "").lower() == "password":
            raise ActionError("Use fill_credential for password fields.")
        loc.fill(text)
        if press_enter:
            self._guard_risky(loc, "submit the form containing")
            loc.press("Enter")
            self._after_action()

    def fill_secret(self, ref: str, value: str) -> None:
        if not self.site_ok(self.page.url):
            raise ActionError("Blocked: credentials are only typed on this portal's own domain.")
        loc = self._el(ref)
        if loc.evaluate("el => el.tagName.toLowerCase()") not in ("input", "textarea"):
            raise ActionError(f"{ref} is not a text field.")
        loc.fill(value)

    def select(self, ref: str, option: str) -> None:
        loc = self._el(ref)
        try:
            loc.select_option(label=option)
        except PWError:
            loc.select_option(value=option)
        self._after_action()

    def download(self, ref: str, label: str) -> Download:
        loc = self._el(ref)
        self._guard_risky(loc, "click (download)")
        data, name = None, None
        try:
            with self.page.expect_download(timeout=15000) as info:
                loc.click()
            dl = info.value
            tmp = self.download_dir / f".partial-{int(time.time() * 1000)}"
            dl.save_as(tmp)
            data, name = tmp.read_bytes(), dl.suggested_filename
            tmp.unlink()
        except PWTimeout:
            href = loc.get_attribute("href")
            if not href:
                raise ActionError("Clicking that did not start a download and it has no link to fetch.")
            url = urljoin(self.page.url, href)
            if not self.site_ok(url):
                raise ActionError("Blocked: that file is hosted outside this session's allowed sites (or on your blocked list).")
            resp = self.context.request.get(url)
            ctype = resp.headers.get("content-type", "")
            if not resp.ok or "html" in ctype:
                raise ActionError(f"That link opened a page ({resp.status}, {ctype}), not a file. Look for a download button.")
            data, name = resp.body(), Path(urlparse(url).path).name or "document"
        return self._store(data, name, label)

    # ---- vision: look at the screen and act by position ----
    def look(self, grid: bool = False, region: tuple[int, int, int, int] | None = None, quality: int = 70) -> bytes:
        kw = {"type": "jpeg", "quality": quality}
        if region:
            x, y, w, h = region
            x, y = max(0, min(x, VIEWPORT["width"] - 10)), max(0, min(y, VIEWPORT["height"] - 10))
            w, h = max(10, min(w, VIEWPORT["width"] - x)), max(10, min(h, VIEWPORT["height"] - y))
            kw["clip"] = {"x": x, "y": y, "width": w, "height": h}
        if grid:
            self.page.evaluate(GRID_JS, True)
        try:
            return self.page.screenshot(**kw)
        finally:
            if grid:
                self.page.evaluate(GRID_JS, False)

    def _point_ok(self, x: int, y: int) -> None:
        if not (0 <= x < VIEWPORT["width"] and 0 <= y < VIEWPORT["height"]):
            raise ActionError(f"({x},{y}) is outside the visible screen (0-{VIEWPORT['width'] - 1}, 0-{VIEWPORT['height'] - 1}). Scroll first.")

    def what_is_at(self, x: int, y: int) -> dict:
        try:
            return self.page.evaluate(POINT_JS, [x, y])
        except PWError:
            return {"tag": "", "text": ""}

    def _guard_point(self, x: int, y: int, purpose: str, verb: str) -> str:
        hit = self.what_is_at(x, y)
        desc = f"{hit.get('tag', '')} \"{hit.get('text', '')}\"".strip()
        if hit.get("type") == "password":
            raise ActionError("That's a password field; use fill_credential.")
        self._gate(verb, hit.get("text", ""), purpose or "", f" at ({x},{y})")
        return desc

    def click_at(self, x: int, y: int, purpose: str, double: bool = False) -> str:
        self._point_ok(x, y)
        desc = self._guard_point(x, y, purpose, "click")
        self.page.mouse.click(x, y, click_count=2 if double else 1)
        self._after_action()
        return desc

    def drag(self, x1: int, y1: int, x2: int, y2: int, purpose: str) -> None:
        self._point_ok(x1, y1)
        self._point_ok(x2, y2)
        self._guard_point(x1, y1, purpose, "drag")
        m = self.page.mouse
        m.move(x1, y1)
        m.down()
        m.move(x2, y2, steps=15)
        m.up()
        self._after_action()

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._point_ok(x, y)
        self.page.mouse.move(x, y)
        self.page.mouse.wheel(max(-5000, min(dx, 5000)), max(-5000, min(dy, 5000)))
        self.page.wait_for_timeout(300)
        self._after_action()

    def press_key(self, key: str) -> None:
        key = "Space" if key == " " else key
        if key not in KEYS:
            raise ActionError(f"Unsupported key {key!r}. Use one of: Enter, Tab, Escape, Backspace, Delete, Space, arrows, PageUp/PageDown, Home, End, KeyA-KeyZ, Digit0-Digit9.")
        if key == "Enter":
            active = self.page.evaluate("() => { const f = document.activeElement && document.activeElement.form; const b = f && f.querySelector('[type=submit],button'); return b ? (b.innerText || b.value || '') : ''; }")
            if active:
                self._gate("press Enter, which submits", active)
        self.page.keyboard.press(" " if key == "Space" else key)
        self._after_action()

    def type_keys(self, text: str) -> None:
        if self.page.evaluate("() => (document.activeElement && document.activeElement.type) === 'password'"):
            raise ActionError("A password field is focused; use fill_credential.")
        self.page.keyboard.type(text[:500], delay=15)

    def wait(self, seconds: float) -> None:
        end = time.time() + max(0.5, min(seconds, 30))
        while time.time() < end:
            self.page.wait_for_timeout(400)
            if self.on_tick:
                self.on_tick()

    def _store(self, data: bytes, name: str, label: str) -> Download:
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.manifest:
            return Download(self.download_dir / self.manifest[digest]["file"], label, digest, True)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "document"
        if not Path(safe).suffix:
            safe += ".pdf" if data[:4] == b"%PDF" else ".bin"
        path = self.download_dir / safe
        n = 1
        while path.exists():
            path = self.download_dir / f"{Path(safe).stem}-{n}{Path(safe).suffix}"
            n += 1
        path.write_bytes(data)
        self.manifest[digest] = {"file": path.name, "label": label, "bytes": len(data),
                                 "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": self.page.url}
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2))
        return Download(path, label, digest, False)
