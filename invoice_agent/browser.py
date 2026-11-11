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

SNAPSHOT_JS = r"""
(maxEls) => {
  const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=tab], [role=menuitem], [onclick], summary';
  const out = [];
  let i = 0;
  document.querySelectorAll('[data-agent-ref]').forEach(el => el.removeAttribute('data-agent-ref'));
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || cs.visibility === 'hidden' || cs.display === 'none' || el.type === 'hidden') continue;
    if (out.length >= maxEls) break;
    const ref = 'e' + (i++);
    el.setAttribute('data-agent-ref', ref);
    const tag = el.tagName.toLowerCase();
    const label = (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || el.id || '';
    const text = (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    const item = { ref, tag, text, label: label.replace(/\s+/g, ' ').trim().slice(0, 60) };
    if (el.type) item.type = el.type;
    if (tag === 'a') item.href = el.getAttribute('href');
    if (tag === 'input' || tag === 'textarea') item.value = el.type === 'password' ? (el.value ? '[filled]' : '') : (el.value || '').slice(0, 40);
    if (tag === 'select') item.options = [...el.options].map(o => o.text).slice(0, 15);
    if (el.disabled) item.disabled = true;
    out.push(item);
  }
  const text = (document.body ? document.body.innerText : '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 5000);
  return { url: location.href, title: document.title, elements: out, text };
}
"""


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
                 secret_values: list[str] | None = None):
        self.allowed = allowed_domains
        self.download_dir = download_dir
        self.headless = headless
        self.executable_path = executable_path
        self.approve = approve or (lambda _msg: False)
        self.secret_values = [s for s in (secret_values or []) if len(s) >= 4]
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
        self.context = self._browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
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
        lines = [f"URL: {snap['url']}", f"Title: {snap['title']}", "", "Interactive elements:"]
        for el in snap["elements"]:
            bits = [f"[{el['ref']}] {el['tag']}"]
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

    def _guard_risky(self, loc, verb: str) -> None:
        desc = self._describe(loc)
        if RISKY.search(desc):
            if not self.approve(f"The agent wants to {verb} \"{desc}\" on {self.page.url}. Allow it?"):
                raise ActionError(f"Blocked: \"{desc}\" looks like it changes something (payment, order, deletion or submission). "
                                  "This agent only reads and downloads. Do not try this control again.")

    def _after_action(self) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except PWTimeout:
            pass
        if not host_allowed(self.page.url, self.allowed):
            bad = self.page.url
            try:
                self.page.go_back(timeout=8000)
            except PWError:
                pass
            raise ActionError(f"Blocked: that led to {urlparse(bad).hostname}, which is outside this portal's allowed domains. Went back.")

    # ---- actions ----
    def navigate(self, url: str) -> None:
        url = urljoin(self.page.url if self.page.url.startswith("http") else "", url)
        if not url.startswith(("http://", "https://")) or not host_allowed(url, self.allowed):
            raise ActionError(f"Blocked: {url} is outside this portal's allowed domains ({', '.join(self.allowed)}).")
        self.page.goto(url, wait_until="domcontentloaded")
        self._after_action()

    def click(self, ref: str) -> None:
        loc = self._el(ref)
        href = loc.get_attribute("href")
        if href and not href.startswith(("#", "javascript:")):
            target = urljoin(self.page.url, href)
            if not host_allowed(target, self.allowed):
                raise ActionError(f"Blocked: that link goes to {urlparse(target).hostname}, outside this portal's allowed domains.")
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
        if not host_allowed(self.page.url, self.allowed):
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
            if not host_allowed(url, self.allowed):
                raise ActionError("Blocked: that file is hosted outside this portal's allowed domains.")
            resp = self.context.request.get(url)
            ctype = resp.headers.get("content-type", "")
            if not resp.ok or "html" in ctype:
                raise ActionError(f"That link opened a page ({resp.status}, {ctype}), not a file. Look for a download button.")
            data, name = resp.body(), Path(urlparse(url).path).name or "document"
        return self._store(data, name, label)

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
