# Pilot (working name)

A web app where you paste a website link and your login, watch an AI agent work in a live browser window, and tell it what to do. The first paid use case is logging into vendor portals for a bookkeeping firm's clients and downloads their invoices and statements, using Claude to drive a real browser.

## Quick start (run it on your own computer)
1. Install **Python 3.11 or newer** from https://www.python.org/downloads/ (on Windows, tick "Add Python to PATH").
2. Unzip this folder.
3. Start it:
   - **Windows:** double-click `run.bat`
   - **Mac / Linux:** open a terminal in the folder and run `./run.sh`
4. Your browser opens at http://127.0.0.1:8000. The first start takes a few minutes while it installs.
5. Click **Watch a demo** to see it work with no key, or paste your **Anthropic API key** (console.anthropic.com) to run real tasks.

Stop it with Ctrl+C in the terminal window. Your key is saved in `data/.env`; downloads go to `data/out/`; step-by-step reports with screenshots go to `data/runs/`.

## Using it
- **Start form:** website link, username, password, optional authenticator secret, and what it should do.
- **Live view:** the agent's browser updates as it works; the activity feed shows each step in plain English.
- **Chat:** send instructions at any time, including after a task finishes.
- **Approvals:** payments, orders, deletions and submissions pause with Allow / Don't allow buttons.
- **Login details** live in memory for that session only, are never sent to the AI, and are wiped when it ends.
- **Cost:** shown live in the top bar; each session stops at $2.00 of AI usage.

## Hosting it online
A `Dockerfile` is included. The server refuses to listen on a public address unless `INVOICE_AGENT_ACCESS_CODE` is set, because every session spends your AI credit.

## Batch mode (command line): what it does
- Reads `portals.toml`: one entry per client account per vendor portal, with the task in plain English.
- Logs in with credentials from an encrypted local vault. **The model never sees a username, password or one-time code**; it picks the field and the code types the value in.
- Generates authenticator (TOTP) codes itself when the firm stores the secret; otherwise asks the bookkeeper.
- Downloads matching documents to `out/<client>/<portal>/`, skipping files it already has (SHA-256 manifest).
- Writes `runs/<time>-<portal>/report.html` with every step, a screenshot per action, files saved and model cost.

## Safety rules (enforced in code, not just the prompt)
| Rule | How |
|---|---|
| Read and download only | Clicks on pay / order / delete / cancel / submit / approve-style controls need a human yes; `--unattended` refuses them |
| Stay on the portal | Navigation, links and file downloads outside `allowed_domains` are refused; off-site redirects are reversed |
| Secrets stay local | Vault values are typed by the browser layer, masked in snapshots, and redacted from anything sent to the model |
| Prompt injection | Page text is wrapped as untrusted `<page_content>`; the hard rules above hold whatever the page says |
| Runaway cost | Step cap per portal and a USD cost cap (`--max-cost`, default $1.50) |

## Setup
```bash
pip install -r requirements.txt
python -m playwright install chromium          # or set INVOICE_AGENT_CHROMIUM to an existing Chromium
export ANTHROPIC_API_KEY=...                   # or `ant auth login`
export INVOICE_AGENT_VAULT_PASSPHRASE=...      # or you'll be prompted
cp portals.example.toml portals.toml           # then edit
python -m invoice_agent vault set rivera-acme username
python -m invoice_agent vault set rivera-acme password
python -m invoice_agent vault set rivera-acme totp_secret   # optional
python -m invoice_agent run --portals portals.toml --show-browser
```
Model: `claude-opus-5-5` at medium effort, with server-side refusal fallback and prompt caching on.

## Tests
`python -m pytest -q tests` runs the whole harness against a local fake portal (login, authenticator code, invoices, a "Pay now" trap and a planted prompt injection) with a scripted stand-in for the model, so no API key is needed.

## Not built yet
- QuickBooks / Xero upload
- Scheduling (monthly runs), multi-user web dashboard, hosted browsers
- Per-portal saved "recipes" to cut model cost on repeat runs
