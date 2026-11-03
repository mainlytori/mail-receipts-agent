from datetime import datetime, timezone
import base64

from openpyxl import load_workbook

from receipt_agent.main import run_once
from receipt_agent.models import Category, Email, ReceiptExtraction, ReceiptRow
from receipt_agent.sinks.excel import ExcelSink
from receipt_agent.sources.gmail import _body_text
from receipt_agent.state import SeenStore
from receipt_agent.text import html_to_text


def make_email(i: int, subject: str = "Your receipt") -> Email:
    return Email(
        provider="gmail",
        account="me@gmail.com",
        message_id=f"id{i}",
        subject=subject,
        sender="Shop <shop@example.com>",
        received_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
        body="Total: $12.50",
        link=f"https://mail.google.com/mail/u/0/#all/id{i}",
    )


class FakeSource:
    name = "gmail"
    account = "me@gmail.com"

    def __init__(self, emails):
        self.emails = emails

    def fetch(self, since_days):
        return iter(self.emails)


class FakeExtractor:
    def __init__(self):
        self.calls = 0

    def extract(self, email):
        self.calls += 1
        if "newsletter" in email.subject:
            return ReceiptExtraction(is_receipt=False)
        return ReceiptExtraction(
            is_receipt=True,
            store="Coffee Place",
            total=12.5,
            currency="usd",
            category=Category.FOOD_DRINKS,
            items=["Latte", "Croissant"],
        )


def test_html_to_text_drops_styles_and_keeps_table_cells():
    raw = "<html><style>.x{}</style><body><table><tr><td>Latte</td><td>$4.00</td></tr></table>" \
          "<p>Total&nbsp;$4.00</p></body></html>"
    text = html_to_text(raw)
    assert ".x" not in text
    assert "Latte | $4.00" in text
    assert "Total $4.00" in text


def test_gmail_body_falls_back_to_html():
    enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode()
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": enc(" ")}},
            {"mimeType": "text/html", "body": {"data": enc("<p>Order total: 9.99 EUR</p>")}},
        ],
    }
    assert _body_text(payload) == "Order total: 9.99 EUR"


def test_row_uses_email_date_when_receipt_has_none():
    row = ReceiptRow.from_extraction(make_email(1), ReceiptExtraction(is_receipt=True, total=3))
    assert row.date == "2026-10-01"
    assert row.category == "Other"
    assert len(row.values()) == len(ReceiptRow.HEADERS)


def test_run_once_writes_receipts_and_skips_seen(tmp_path):
    sink = ExcelSink(str(tmp_path / "out.xlsx"))
    seen = SeenStore(str(tmp_path / "seen.json"))
    extractor = FakeExtractor()
    source = FakeSource([make_email(1), make_email(2, "Weekly newsletter")])

    assert run_once([source], sink, extractor, seen, 30, dry_run=False) == 1
    # Second run: nothing new, the AI isn't called again.
    assert run_once([source], sink, extractor, SeenStore(str(tmp_path / "seen.json")), 30, False) == 0
    assert extractor.calls == 2

    ws = load_workbook(tmp_path / "out.xlsx")["Receipts"]
    assert [c.value for c in ws[1]] == ReceiptRow.HEADERS
    assert ws.max_row == 2
    assert ws.cell(2, 2).value == "Coffee Place"
    assert ws.cell(2, 4).value == "USD"
    assert ws.cell(2, 8).value == "Latte; Croissant"
    assert sink.existing_keys() == {"gmail:me@gmail.com:id1"}


def test_rows_already_in_table_are_not_reread(tmp_path):
    sink = ExcelSink(str(tmp_path / "out.xlsx"))
    sink.append([ReceiptRow.from_extraction(make_email(1), ReceiptExtraction(is_receipt=True))])
    extractor = FakeExtractor()
    run_once([FakeSource([make_email(1)])], sink, extractor, SeenStore(str(tmp_path / "s.json")), 30, False)
    assert extractor.calls == 0


from receipt_agent.rules_extractor import RulesExtractor


def test_rules_extractor_reads_a_simple_receipt():
    email = make_email(5, "Your Wolt order receipt")
    email.sender = "Wolt <info@wolt.com>"
    email.body = "Order number: AB-12345\nSubtotal 4 500 тг\nTotal: 5 200 тг\nPaid with Visa ending 4242"
    r = RulesExtractor().extract(email)
    assert r.is_receipt
    assert r.total == 5200
    assert r.currency == "KZT"
    assert r.store == "Wolt"
    assert r.order_number == "AB-12345"
    assert r.category == Category.FOOD_DRINKS
    assert r.payment_method == "Visa ****4242"


def test_rules_extractor_handles_decimals_and_symbols():
    email = make_email(6, "Receipt from Coffee Place")
    email.body = "Latte | $4.00\nTax | $0.35\nTotal | $1,234.50"
    r = RulesExtractor().extract(email)
    assert (r.total, r.currency, r.tax) == (1234.5, "USD", 0.35)


def test_rules_extractor_skips_newsletters():
    email = make_email(7, "Weekly news")
    email.body = "Check out our new arrivals!"
    assert RulesExtractor().extract(email).is_receipt is False


import json as _json

from receipt_agent import extractor as ollama_mod


class _Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


def test_ollama_extractor_sends_schema_and_parses_answer(monkeypatch):
    sent = {}
    answer = {"is_receipt": True, "store": "Wolt", "total": 5200, "currency": "KZT",
              "category": "Food & Drinks", "items": ["Pizza"]}
    monkeypatch.setattr(ollama_mod.requests, "get",
                        lambda url, timeout: _Resp({"models": [{"name": "qwen2.5:7b"}]}))

    def fake_post(url, json, timeout):
        sent.update(json)
        return _Resp({"message": {"content": _json.dumps(answer)}})

    monkeypatch.setattr(ollama_mod.requests, "post", fake_post)
    r = ollama_mod.OllamaExtractor("qwen2.5:7b").extract(make_email(8))
    assert r.store == "Wolt" and r.total == 5200 and r.category == Category.FOOD_DRINKS
    assert "$ref" not in _json.dumps(sent["format"])
    assert sent["stream"] is False


def test_ollama_extractor_explains_missing_model(monkeypatch):
    monkeypatch.setattr(ollama_mod.requests, "get", lambda url, timeout: _Resp({"models": []}))
    try:
        ollama_mod.OllamaExtractor("qwen2.5:7b")
    except ollama_mod.OllamaError as e:
        assert "ollama pull qwen2.5:7b" in str(e)
    else:
        raise AssertionError("expected OllamaError")


def test_ollama_bad_answer_is_skipped(monkeypatch):
    monkeypatch.setattr(ollama_mod.requests, "get",
                        lambda url, timeout: _Resp({"models": [{"name": "qwen2.5:7b"}]}))
    monkeypatch.setattr(ollama_mod.requests, "post",
                        lambda url, json, timeout: _Resp({"message": {"content": "not json"}}))
    assert ollama_mod.OllamaExtractor("qwen2.5:7b").extract(make_email(9)) is None


def test_fix_up_fills_store_and_corrects_currency():
    email = make_email(10, "Your receipt from Anthropic")
    email.sender = "Anthropic <invoice+statements@mail.anthropic.com>"
    email.body = "Amount paid $23.20"
    r = ollama_mod.fix_up(email, ReceiptExtraction(is_receipt=True, total=23.2, currency="KZT"))
    assert r.currency == "USD"
    assert r.store == "Anthropic"


def test_fix_up_keeps_correct_currency_and_skips_generic_sender():
    email = make_email(11, "Чек")
    email.sender = "OFD Receipts <noreply@ofd.kz>"
    email.body = "Итого: 4 993 ₸"
    r = ollama_mod.fix_up(email, ReceiptExtraction(is_receipt=True, total=4993, currency="KZT"))
    assert r.currency == "KZT"
    assert r.store is None


def test_ollama_schema_requires_store_and_items(monkeypatch):
    monkeypatch.setattr(ollama_mod.requests, "get",
                        lambda url, timeout: _Resp({"models": [{"name": "qwen2.5:7b"}]}))
    ex = ollama_mod.OllamaExtractor("qwen2.5:7b")
    assert {"store", "items", "currency"} <= set(ex.schema["required"])


def test_sheet_link_or_id_both_work():
    from receipt_agent.sinks.google_sheets import _sheet_id
    assert _sheet_id("https://docs.google.com/spreadsheets/d/1AbC_d-9/edit#gid=0") == "1AbC_d-9"
    assert _sheet_id(" 1AbC_d-9 ") == "1AbC_d-9"
