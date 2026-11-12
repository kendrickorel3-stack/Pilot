# Pilot (working name)

A web app where you paste a website link and your login, watch an AI agent work in a live browser window, and tell it what to do. The first paid use case is logging into vendor portals for a bookkeeping firm's clients to download their invoices and statements. The AI can be OpenAI or Claude.

## Quick start (run it on your own computer)
1. Install **Python 3.11 or newer** from https://www.python.org/downloads/ (on Windows, tick "Add Python to PATH").
2. Unzip this folder.
3. Start it:
   - **Windows:** double-click `run.bat`
   - **Mac / Linux:** open a terminal in the folder and run `./run.sh`
4. Your browser opens at http://127.0.0.1:8000. The first start takes a few minutes while it installs.
5. Click **Watch a demo** to see it work with no key, or choose **OpenAI** or **Anthropic (Claude)**, paste your API key and pick a model to run real tasks. The key is checked before it's saved.

Stop it with Ctrl+C in the terminal window. Your key and model choice are saved in `data/.env`; downloads go to `data/out/`; step-by-step reports with screenshots go to `data/runs/`.

## Using it
- **Start form:** website link, username, password, optional authenticator secret, and what it should do.
- **Live view:** the agent's browser updates as it works; the activity feed shows each step in plain English.
- **Chat:** send instructions at any time, including after a task finishes.
- **Approvals:** payments, orders, deletions and submissions pause with Allow / Don't allow buttons.
- **Login details** live in memory for that session only, are never sent to the AI, and are wiped when it ends.
- **Screen vision:** on pages drawn as pictures (games, maps, charts, canvas apps) the agent takes screenshots and clicks, drags, scrolls and types by position. It zooms in to read small numbers and timers, and waits for timers to run. Every on-screen action returns a fresh screenshot so it can check the result.
- **Take control:** click the live view (or "Take control") to drive the real browser yourself: click, scroll, type, keys, Back. "Hand back to agent" lets it carry on from where you left the page.
- **Purchases:** anything that pays, buys, orders, confirms, deletes or submits waits for your approval, including purchase pop-ups drawn on a canvas, as long as the agent's stated purpose or the clicked element says so. On canvas pages Pilot can't read the button itself, so check the live view.
- **Cost:** shown live in the top bar; each session stops at $2.00 of AI usage.

## Rules (data/rules/)
Click **Rules** in the top bar, or edit the files directly. The agent reads them before every task; changes apply to the next task.

| File | What it does |
|---|---|
| `instructions.md` | Plain sentences the agent reads first: how to work, what to avoid, what matters |
| `settings.toml` | `ask_before` words that need your OK, `auto_approve_sites`, `never_allow` words, cost and step limits, `also_allow` / `blocked` sites |
| `sites/<domain>.md` | Notes for one website (where things are, what numbers and timers mean), given to the agent when it arrives there |

If a file is missing or broken, Pilot uses safe defaults and tells you. A few protections are fixed in code: the AI never sees passwords or codes, logins are typed only on the session's own site, no getting past CAPTCHAs or bot checks, website text can't give orders, and every session has a cost cap.

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
export OPENAI_API_KEY=...                      # or ANTHROPIC_API_KEY=...
export INVOICE_AGENT_VAULT_PASSPHRASE=...      # or you'll be prompted
cp portals.example.toml portals.toml           # then edit
python -m invoice_agent vault set rivera-acme username
python -m invoice_agent vault set rivera-acme password
python -m invoice_agent vault set rivera-acme totp_secret   # optional
python -m invoice_agent run --portals portals.toml --show-browser
```
AI providers (see `invoice_agent/llm.py`): OpenAI (default model `gpt-6.1-sol`, or set `OPENAI_MODEL`) or Anthropic (`claude-opus-5-5`). Set `PILOT_PROVIDER=openai|anthropic` to choose when both keys are present.

## Tests
`python -m pytest -q tests` runs the whole harness against a local fake portal (login, authenticator code, invoices, a "Pay now" trap and a planted prompt injection) with a scripted stand-in for the model, so no API key is needed.

## Not built yet
- QuickBooks / Xero upload
- Scheduling (monthly runs), multi-user web dashboard, hosted browsers
- Per-portal saved "recipes" to cut model cost on repeat runs
