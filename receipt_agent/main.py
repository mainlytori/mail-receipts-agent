"""Command line entry point: python -m receipt_agent"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

from .extractor import ReceiptExtractor
from .models import ReceiptRow
from .state import SeenStore

log = logging.getLogger("receipt_agent")


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def build_sources():
    sources = []
    creds = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    google_token = os.getenv("GOOGLE_TOKEN_FILE", "token_google.json")
    if env_flag("GMAIL_ENABLED", True):
        from .sources.gmail import GmailSource

        query = os.getenv("GMAIL_QUERY")
        sources.append(
            GmailSource(creds, google_token, query) if query else GmailSource(creds, google_token)
        )
    if env_flag("OUTLOOK_ENABLED", False):
        from .sources.outlook import OutlookSource

        client_id = os.getenv("OUTLOOK_CLIENT_ID")
        if not client_id:
            sys.exit("OUTLOOK_ENABLED is on but OUTLOOK_CLIENT_ID is empty. See the README.")
        sources.append(
            OutlookSource(
                client_id,
                os.getenv("OUTLOOK_TOKEN_FILE", "token_outlook.json"),
                os.getenv("OUTLOOK_AUTHORITY", "common"),
            )
        )
    if not sources:
        sys.exit("No inbox is turned on. Set GMAIL_ENABLED or OUTLOOK_ENABLED in .env.")
    return sources


def build_sink():
    output = os.getenv("OUTPUT", "sheets").lower()
    if output == "excel":
        from .sinks.excel import ExcelSink

        return ExcelSink(os.getenv("EXCEL_PATH", "expenses.xlsx"))
    if output == "sheets":
        from .sinks.google_sheets import GoogleSheetsSink

        return GoogleSheetsSink(
            os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
            os.getenv("GOOGLE_TOKEN_FILE", "token_google.json"),
            os.getenv("GOOGLE_SHEET_ID") or None,
        )
    sys.exit(f"OUTPUT must be 'sheets' or 'excel', not {output!r}")


def run_once(sources, sink, extractor, seen: SeenStore, days: int, dry_run: bool) -> int:
    already_in_table = set() if dry_run else sink.existing_keys()
    rows: list[ReceiptRow] = []
    checked = 0
    for source in sources:
        log.info("Checking %s (%s), last %d days", source.name, source.account, days)
        for email in source.fetch(days):
            if email.key in seen or email.key in already_in_table:
                continue
            checked += 1
            result = extractor.extract(email)
            if result is None:
                continue  # skipped (logged); try again next run
            if result.is_receipt:
                row = ReceiptRow.from_extraction(email, result)
                log.info("Receipt: %s  %s %s  %s", row.date, row.total, row.currency, row.store)
                rows.append(row)
            if not dry_run:
                seen.add(email.key)
    if dry_run:
        for r in rows:
            print(r.values())
    else:
        sink.append(rows)
        seen.save()
    log.info("Checked %d new emails, added %d receipts", checked, len(rows))
    return len(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="receipt_agent",
        description="Find receipts in your Gmail / Outlook and add them to a spreadsheet.",
    )
    parser.add_argument("--days", type=int, default=None, help="how far back to look (default 30)")
    parser.add_argument(
        "--every", type=int, metavar="MINUTES", help="keep running and check every N minutes"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print receipts instead of writing them"
    )
    args = parser.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "googleapiclient", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is missing. Add it to your .env file (see the README).")

    days = args.days or int(os.getenv("LOOKBACK_DAYS", "30"))
    sources = build_sources()
    sink = None if args.dry_run else build_sink()
    extractor = ReceiptExtractor(os.getenv("CLAUDE_MODEL", "claude-opus-5-5"))
    seen = SeenStore(os.getenv("STATE_FILE", "seen_emails.json"))

    while True:
        run_once(sources, sink, extractor, seen, days, args.dry_run)
        if not args.every:
            break
        log.info("Next check in %d minutes (Ctrl+C to stop)", args.every)
        time.sleep(args.every * 60)
        days = 2  # after the first pass, only look at recent mail


if __name__ == "__main__":
    main()
