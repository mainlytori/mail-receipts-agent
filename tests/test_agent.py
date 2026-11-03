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
    email = make_email(8)
    email.body = "Total: 5 200 ₸"
    r = ollama_mod.OllamaExtractor("qwen2.5:7b").extract(email)
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
    email = make_email(9, "Hello")
    email.body = "Just saying hi, lunch was 5 USD"
    assert ollama_mod.OllamaExtractor("qwen2.5:7b").extract(email) is None


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


def test_fix_up_cleans_small_model_mistakes():
    email = make_email(12, "Чек KFC N-City")
    email.sender = "KFC <noreply@kfc.kz>"
    email.body = "Твистер 1 шт\nИтого: 4 376,20 ₸\nОплата: Visa ****1111"
    r = ollama_mod.fix_up(email, ReceiptExtraction(
        is_receipt=True, store="KFC N-City (KFC N-City)", total=None, currency="KZT",
        category=Category.GROCERIES, items=["null", "Total", "KFC N-City x1", "Twister"],
    ))
    assert r.store == "KFC N-City"
    assert r.total == 4376.2
    assert r.category == Category.FOOD_DRINKS
    assert r.items == ["Twister"]
    assert r.payment_method == "Visa ****1111"


def test_sheet_link_or_id_both_work():
    from receipt_agent.sinks.google_sheets import _sheet_id
    assert _sheet_id("https://docs.google.com/spreadsheets/d/1AbC_d-9/edit#gid=0") == "1AbC_d-9"
    assert _sheet_id(" 1AbC_d-9 ") == "1AbC_d-9"


def test_excel_goes_into_chosen_folder(tmp_path, monkeypatch):
    from receipt_agent import main as m
    monkeypatch.delenv("EXCEL_PATH", raising=False)
    monkeypatch.setenv("OUTPUT_FOLDER", str(tmp_path))
    assert m.excel_path(choose=False) == tmp_path / "Expenses.xlsx"


def test_folder_picker_asks_when_no_folder_and_saves_it(tmp_path, monkeypatch):
    from receipt_agent import folder_picker, main as m
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXCEL_PATH", raising=False)
    monkeypatch.delenv("OUTPUT_FOLDER", raising=False)
    target = tmp_path / "My Receipts"
    target.mkdir()
    import builtins
    monkeypatch.setattr(builtins, "input", lambda prompt="": str(target))
    monkeypatch.setitem(__import__("sys").modules, "tkinter", None)  # no window available
    assert m.excel_path(choose=False) == target.resolve() / "Expenses.xlsx"
    assert f"OUTPUT_FOLDER={target.resolve()}" in (tmp_path / ".env").read_text()


def test_rules_total_with_dots_pipes_and_line_breaks():
    for body in ["ИТОГО ........ 4 993,00 ₸", "Итого | | 4 763 тг", "ИТОГО:\n4 376,20\nKZT"]:
        email = make_email(13, "Чек")
        email.body = body
        r = RulesExtractor().extract(email)
        assert r.is_receipt and r.total in (4993, 4763, 4376.2), body


def test_ollama_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(ollama_mod.requests, "get",
                        lambda url, timeout: _Resp({"models": [{"name": "qwen2.5:7b"}]}))
    def boom(url, json, timeout):
        raise ollama_mod.requests.Timeout("too slow")
    monkeypatch.setattr(ollama_mod.requests, "post", boom)
    email = make_email(14, "Your receipt")
    email.body = "Total: 9.99 USD"
    r = ollama_mod.OllamaExtractor("qwen2.5:7b").extract(email)
    assert r.is_receipt and r.total == 9.99


def test_fix_up_category_and_order_number_even_when_rules_unsure():
    email = make_email(15, "Hardee's EXPO Mall")
    email.sender = "Hardee's <info@hardees.kz>"
    email.body = "Спасибо! 4 993 ₸"
    long_order = "Oct 9 - Nov 9  $23.20  Visa 4242  https://pay.stripe.com/receipts/abc"
    r = ollama_mod.fix_up(email, ReceiptExtraction(
        is_receipt=True, store="Hardee's", total=4993, currency="KZT",
        category=Category.OTHER, order_number=long_order))
    assert r.category == Category.FOOD_DRINKS
    assert r.order_number is None


def test_long_email_is_shortened_for_the_ai():
    assert len(ollama_mod._short("x" * 50000)) < 6100


def test_wolt_style_total_with_hidden_mark():
    email = make_email(16, "Your order's delivered: Okadzaki")
    email.body = "| Item | | KZT ‎1,200.00 | |\n| Total | | KZT ‎4,763.00 | |"
    from receipt_agent.rules_extractor import details
    d = details(email)
    assert (d.total, d.currency) == (4763.0, "KZT")
    r = ollama_mod.fix_up(email, ReceiptExtraction(is_receipt=True, store="Okadzaki", total=None, currency=None))
    assert (r.total, r.currency) == (4763.0, "KZT")


def test_delivery_app_store_comes_from_subject_and_no_total_is_not_receipt():
    email = make_email(17, "Your order's delivered: Hardee's EXPO Mall 01.11")
    email.sender = "Wolt <info@wolt.com>"
    email.body = "| Total | | KZT ‎4,993.00 | |"
    r = ollama_mod.fix_up(email, ReceiptExtraction(is_receipt=True, store="Wolt", total=None))
    assert r.store == "Hardee's EXPO Mall"
    assert r.total == 4993.0 and r.is_receipt
    from receipt_agent.rules_extractor import RulesExtractor as RX
    assert RX().extract(email).store == "Hardee's EXPO Mall"

    promo = make_email(18, "KFC: new Twister deal!")
    promo.sender = "KFC <news@kfc.kz>"
    promo.body = "Try the new Twister today, receipt-worthy taste"
    p = ollama_mod.fix_up(promo, ReceiptExtraction(is_receipt=True, store="KFC", total=None))
    assert p.is_receipt is False


def test_emails_without_any_amount_skip_the_ai(monkeypatch):
    monkeypatch.setattr(ollama_mod.requests, "get",
                        lambda url, timeout: _Resp({"models": [{"name": "qwen2.5:7b"}]}))
    def never(*a, **k):
        raise AssertionError("AI should not be called")
    monkeypatch.setattr(ollama_mod.requests, "post", never)
    email = make_email(19, "Your order is on its way")
    email.body = "The courier is coming!"
    assert ollama_mod.OllamaExtractor("qwen2.5:7b").extract(email).is_receipt is False


def test_run_keeps_receipts_found_before_connection_drops(tmp_path):
    class Flaky(FakeSource):
        def fetch(self, since_days):
            yield make_email(20)
            raise ConnectionError("dns blip")
    sink = ExcelSink(str(tmp_path / "out.xlsx"))
    n = run_once([Flaky([])], sink, FakeExtractor(), SeenStore(str(tmp_path / "s.json")), 30, False)
    assert n == 1
