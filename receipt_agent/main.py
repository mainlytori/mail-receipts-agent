"""Command line entry point: python -m receipt_agent"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

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


def excel_path(choose: bool) -> Path:
    """Where the Excel table lives: EXCEL_PATH, or Expenses.xlsx in the chosen folder."""
    if os.getenv("EXCEL_PATH") and not choose:
        return Path(os.environ["EXCEL_PATH"]).expanduser()
    folder = os.getenv("OUTPUT_FOLDER")
    if choose or not folder or not Path(folder).expanduser().is_dir():
        from .folder_picker import choose_folder

        folder = str(choose_folder())
        os.environ["OUTPUT_FOLDER"] = folder
    return Path(folder).expanduser() / "Expenses.xlsx"


def build_sink(choose_folder: bool = False):
    output = os.getenv("OUTPUT", "excel").lower()
    if output == "excel":
        from .sinks.excel import ExcelSink

        path = excel_path(choose_folder)
        log.info("Saving receipts to %s", path)
        return ExcelSink(str(path))
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
        "--every", type=int, metavar="MINUTES", help="keep running and check every N minutes (0 = run once)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print receipts instead of writing them"
    )
    parser.add_argument(
        "--choose-folder",
        action="store_true",
        help="pick (or change) the folder where the Excel table is saved",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "googleapiclient", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    reader = os.getenv("READER", "ollama").lower()
    if reader == "rules":
        from .rules_extractor import RulesExtractor

        extractor = RulesExtractor()
    elif reader == "ollama":
        from .extractor import OllamaError, OllamaExtractor

        try:
            extractor = OllamaExtractor(
                os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
                os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            )
        except OllamaError as e:
            sys.exit(str(e))
    else:
        sys.exit(f"READER must be 'ollama' or 'rules', not {reader!r}")

    days = args.days or int(os.getenv("LOOKBACK_DAYS", "30"))
    every = args.every if args.every is not None else int(os.getenv("CHECK_EVERY_MINUTES") or 0)
    sources = build_sources()
    sink = None if args.dry_run else build_sink(args.choose_folder)
    seen = SeenStore(os.getenv("STATE_FILE", "seen_emails.json"))

    while True:
        run_once(sources, sink, extractor, seen, days, args.dry_run)
        if not every:
            break
        log.info("Next check in %d minutes (Ctrl+C to stop)", every)
        time.sleep(every * 60)
        days = 2  # after the first pass, only look at recent mail


if __name__ == "__main__":
    main()
