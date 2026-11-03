# Receipt Agent

Finds receipts in your **Gmail** and **Outlook** inboxes and adds them to a spreadsheet, so your expenses are organized without typing anything.

It's **completely free**: the AI runs on your own computer through [Ollama](https://ollama.com), so there are no API keys and no per-email costs.

For every new email that looks like a purchase, the AI reads it and fills in one row:

| Date | Store | Total | Currency | Tax | Category | Payment method | Items | Order number | Email account | Email subject | Link to email |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-28 | Coffee Place | 12.50 | USD | 1.00 | Food & Drinks | Visa ****1234 | Latte; Croissant | A-10293 | me@gmail.com | Your receipt | Open email |

Categories: Groceries, Food & Drinks, Shopping, Transport, Bills, Subscriptions, Health, Travel, Entertainment, Other.

The table can be a **Google Sheet** (default: free, works on your phone, easy to share) or an **Excel file** on your computer.

## How it works

1. Each inbox is searched for likely receipt emails (words like *receipt*, *order*, *invoice*, *payment*).
2. A local AI model reads each one and decides whether money was actually paid. Ads, newsletters and shipping updates are skipped.
3. Receipts are added as new rows. Every email is read only once, so running it again never makes duplicates.

Access is **read-only**: the agent can't send, delete or change your emails.

## Setup (about 15 minutes, once)

You need Python 3.10 or newer and a computer with at least 8 GB of RAM (16 GB recommended).

```bash
git clone <this repo>
cd receipt-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

### 1. Install Ollama (the free AI)

1. Download and install Ollama from [ollama.com/download](https://ollama.com/download) (Mac, Windows or Linux) and open it.
2. In a terminal, download the AI model (about 4.7 GB, once):

   ```bash
   ollama pull qwen2.5:7b
   ```

That's it, nothing to sign up for. Ollama must be running whenever the agent runs.

**Which model?** `qwen2.5:7b` (default) is a good balance and handles English and Russian receipts. It works best with 16 GB of RAM. On a computer with 8 GB, use the smaller `qwen2.5:3b` (`ollama pull qwen2.5:3b`, then set `OLLAMA_MODEL=qwen2.5:3b` in `.env`). It's faster but makes more mistakes. Any other Ollama model works too.

**No AI at all:** set `READER=rules` to use fixed text rules instead. They look for words like "Total" and "Order number". This is very fast and needs no Ollama, but it misses unusual receipts and usually leaves the items empty.

### 2. Set up Google (for Gmail and/or Google Sheets)

1. Open [console.cloud.google.com](https://console.cloud.google.com), create a project (any name).
2. In **APIs & Services → Library**, enable **Gmail API** and **Google Sheets API**.
3. In **APIs & Services → OAuth consent screen**, choose **External**, fill in the app name and your email, and add yourself under **Test users**.
4. In **APIs & Services → Credentials**, click **Create credentials → OAuth client ID**, choose **Desktop app**, and download the JSON.
5. Save it in this folder as `credentials.json`.

The first run opens your browser to sign in to Google. After that it remembers you (in `token_google.json`).

### 3. Set up Outlook (optional)

Works with Outlook.com, Hotmail, Live and Microsoft 365 accounts.

1. Open [portal.azure.com](https://portal.azure.com) → **App registrations** → **New registration**.
2. Name it anything. Under *Supported account types* pick **Accounts in any organizational directory and personal Microsoft accounts**. Click **Register**.
3. Copy the **Application (client) ID** into `.env` as `OUTLOOK_CLIENT_ID=...`.
4. Go to **Authentication**, turn on **Allow public client flows**, and save.
5. Set `OUTLOOK_ENABLED=true` in `.env`.

The first run prints a short code and a link. Open the link, type the code and sign in. After that it remembers you (in `token_outlook.json`).

### 4. Choose where receipts go

- **Google Sheets** (default): leave `OUTPUT=sheets`. If `GOOGLE_SHEET_ID` is empty, the first run creates a sheet called *Expenses (from email receipts)* and prints its link and ID. Put the ID in `.env` so later runs use the same sheet. To use your own sheet, copy the ID from its URL: `docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
- **Excel**: set `OUTPUT=excel` and, if you like, `EXCEL_PATH=expenses.xlsx`. Close the file in Excel before running.

## Run it

You choose: run it yourself whenever you want, or let it check automatically as often as you like.

**Automatically:** set `CHECK_EVERY_MINUTES` in `.env` (for example `60` for every hour, `1440` for once a day) and start it once with `python -m receipt_agent`. It keeps running and checks on that schedule until you press Ctrl+C.

**Yourself:** leave `CHECK_EVERY_MINUTES=0` and run the command whenever you want an update.

```bash
python -m receipt_agent              # check now (last 30 days)
python -m receipt_agent --days 90    # look further back
python -m receipt_agent --dry-run    # just print what it finds, write nothing
python -m receipt_agent --every 60   # check every hour (overrides .env)
python -m receipt_agent --every 0    # check once now, even if .env has a schedule
```

To run it automatically without leaving a window open, schedule `python -m receipt_agent --days 2` with cron (Mac/Linux) or Task Scheduler (Windows).

## Settings

All settings live in `.env` (see `.env.example`):

| Setting | Meaning | Default |
|---|---|---|
| `READER` | `ollama` (free local AI) or `rules` (no AI) | `ollama` |
| `OLLAMA_MODEL` | Which Ollama model reads the emails | `qwen2.5:7b` |
| `OLLAMA_HOST` | Where Ollama is running | `http://localhost:11434` |
| `OUTPUT` | `sheets` or `excel` | `sheets` |
| `GOOGLE_SHEET_ID` | Sheet to write to | creates one |
| `EXCEL_PATH` | Excel file to write to | `expenses.xlsx` |
| `GMAIL_ENABLED` | Check Gmail | `true` |
| `OUTLOOK_ENABLED` | Check Outlook | `false` |
| `OUTLOOK_CLIENT_ID` | Azure app ID for Outlook | |
| `CHECK_EVERY_MINUTES` | Check automatically every N minutes; `0` = run once | `0` |
| `LOOKBACK_DAYS` | Days to look back | `30` |
| `GMAIL_QUERY` | Gmail search used to find candidates | receipt keywords |

## Privacy

Everything runs on your computer, including the AI. Your emails are never sent to any AI company. Your passwords are never stored; sign-in tokens stay in local files that `.gitignore` keeps out of git.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

Code layout:

- `receipt_agent/sources/` reads Gmail and Outlook
- `receipt_agent/extractor.py` asks the local Ollama model to pull out the receipt fields
- `receipt_agent/rules_extractor.py` is the no-AI version
- `receipt_agent/sinks/` writes to Google Sheets or Excel
- `receipt_agent/main.py` ties it together
