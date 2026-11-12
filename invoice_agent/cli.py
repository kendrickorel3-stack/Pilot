"""Command line: store credentials and run portals.

    python -m invoice_agent vault set acme-supplies username
    python -m invoice_agent run --portals portals.toml
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from .config import load_portals
from .vault import FIELDS, Vault, VaultError


def _vault(path: str) -> Vault:
    phrase = os.environ.get("INVOICE_AGENT_VAULT_PASSPHRASE") or getpass.getpass("Vault passphrase: ")
    return Vault(path, phrase)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="invoice_agent", description="Download invoices from vendor portals.")
    ap.add_argument("--vault", default="vault.enc", help="Encrypted credential file (default: vault.enc)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    vs = sub.add_parser("vault", help="Manage stored credentials")
    vsub = vs.add_subparsers(dest="vcmd", required=True)
    vset = vsub.add_parser("set", help="Store a credential (you are prompted for the value)")
    vset.add_argument("portal_id")
    vset.add_argument("field", choices=FIELDS)
    vsub.add_parser("list", help="List portals and which fields are stored (never values)")

    run = sub.add_parser("run", help="Run the agent on one or all portals")
    run.add_argument("--portals", default="portals.toml")
    run.add_argument("--portal", help="Only this portal id")
    run.add_argument("--out", default="out", help="Where invoices are saved")
    run.add_argument("--runs", default="runs", help="Where logs, screenshots and reports go")
    run.add_argument("--show-browser", action="store_true", help="Show the browser window")
    run.add_argument("--unattended", action="store_true", help="Never ask questions; block risky clicks automatically")
    run.add_argument("--max-cost", type=float, default=1.50, help="Model cost cap per portal in USD")
    run.add_argument("--rules", default="rules", help="Rules folder (instructions.md, settings.toml, sites/)")
    run.add_argument("--chromium", default=os.environ.get("INVOICE_AGENT_CHROMIUM"), help="Path to a Chromium binary")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "vault":
            v = _vault(a.vault)
            if a.vcmd == "set":
                v.set(a.portal_id, a.field, getpass.getpass(f"{a.field} for {a.portal_id}: "))
                v.save()
                print(f"Saved {a.field} for {a.portal_id}.")
            else:
                for pid in v.portals():
                    print(f"{pid}: {', '.join(v.fields(pid))}")
            return 0

        from .agent import run_portal  # imported late so vault commands work without the browser stack
        from .policy import load_policy

        portals = load_portals(a.portals)
        if a.portal:
            portals = [p for p in portals if p.id == a.portal]
            if not portals:
                print(f"No portal with id {a.portal!r} in {a.portals}.", file=sys.stderr)
                return 2
        v = _vault(a.vault)
        ask = None if a.unattended else (lambda q: input(f"\n[agent asks] {q}\n> ").strip())
        approve = (lambda _m: False) if a.unattended else (lambda m: input(f"\n[approval] {m} [y/N] ").strip().lower() == "y")
        failures = 0
        for p in portals:
            print(f"\n=== {p.client} / {p.name} ===")
            pol = load_policy(a.rules, create=True)
            for problem in pol.problems:
                print(f"Rules: {problem}")
            r = run_portal(p, v, out_dir=Path(a.out), runs_dir=Path(a.runs), ask_human=ask, approve=approve,
                           headless=not a.show_browser, executable_path=a.chromium, max_cost_usd=a.max_cost, policy=pol)
            print(f"{r.status.upper()}: {r.summary}")
            print(f"{len(r.downloads)} new file(s), {r.steps} steps, ${r.cost_usd:.3f}. Report: {r.run_dir / 'report.html'}")
            failures += r.status != "success"
        return 1 if failures else 0
    except (VaultError, ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
