"""Free extractor that uses fixed text rules instead of AI.

It costs nothing to run but only understands receipts that use common wording
("Total", "Amount paid", ...). Items and tax are often left empty.
"""

from __future__ import annotations

import re

from .models import Category, Email, ReceiptExtraction

SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₸": "KZT", "₽": "RUB", "¥": "JPY", "₹": "INR", "₺": "TRY"}
CODES = "USD|EUR|GBP|KZT|RUB|JPY|INR|TRY|CAD|AUD|CHF|CNY|UAH|UZS|KGS|AED|PLN"
AMOUNT = r"(\d{1,3}(?:[ ,. ]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
MONEY = rf"(?:(?P<sym>[$€£₸₽¥₹₺])\s?|(?P<code>{CODES})\s?)?(?P<amt>{AMOUNT})\s?(?:(?P<sym2>[$€£₸₽¥₹₺])|(?P<code2>{CODES})|тг|руб)?"

TOTAL_RE = re.compile(
    r"(?:grand\s+total|order\s+total|total\s+paid|amount\s+paid|amount\s+charged|total"
    r"|итого|сумма\s+к\s+оплате|к\s+оплате|всего|оплачено)"
    # anything that isn't a number between the word and the amount: ":", "|", dots, line breaks
    r"[^\d$€£₸₽¥₹₺]{0,40}?" + MONEY,
    re.IGNORECASE,
)
TAX_RE = re.compile(r"(?:tax|vat|gst|ндс)\s*(?:\(\s*\d+%\s*\))?\s*[:\-]?\s*(?:\|\s*)?" + MONEY, re.IGNORECASE)
ORDER_RE = re.compile(
    r"(?:order|invoice|receipt|заказ|чек)\s*(?:number|no\.?|#|№|id)\s*[:\-]?\s*#?\s*([A-Z0-9][A-Z0-9\-]{3,})",
    re.IGNORECASE,
)
CARD_RE = re.compile(
    r"(visa|mastercard|master card|amex|american express|paypal|apple pay|google pay|kaspi)"
    r"(?:[^\n\d]{0,20}(\d{4}))?",
    re.IGNORECASE,
)
RECEIPT_WORDS = re.compile(
    r"receipt|order confirm|your order|invoice|payment (?:received|confirm)|thank you for your (?:order|purchase)"
    r"|чек|заказ|оплат",
    re.IGNORECASE,
)

CATEGORY_WORDS = {
    Category.GROCERIES: "grocery|supermarket|market|magnum|small|walmart|tesco|lidl|aldi|instacart",
    Category.FOOD_DRINKS: "restaurant|cafe|coffee|starbucks|pizza|burger|hardee|sushi|суши|бургер|кафе|ресторан|doner|донер|wolt|glovo|uber eats|deliveroo|doordash|kfc|mcdonald",
    Category.TRANSPORT: "uber|bolt|lyft|yandex go|taxi|metro|fuel|parking",
    Category.SUBSCRIPTIONS: "subscription|anthropic|claude pro|openai|chatgpt|midjourney|canva|adobe|google one|yandex plus|netflix|spotify|apple\\.com|icloud|youtube premium|patreon|membership|renewal",
    Category.BILLS: "electric|water bill|internet|mobile plan|utility|beeline|kcell|tele2|rent",
    Category.HEALTH: "pharmacy|apteka|clinic|dental|doctor|health",
    Category.TRAVEL: "airline|air astana|booking\\.com|airbnb|hotel|flight|expedia|ticket",
    Category.ENTERTAINMENT: "cinema|steam|playstation|xbox|concert|ticketmaster",
    Category.SHOPPING: "amazon|aliexpress|ebay|wildberries|ozon|kaspi|shop|store|zara|h&m",
}


def _to_number(raw: str) -> float | None:
    s = raw.replace(" ", " ").strip()
    if re.search(r"[.,]\d{1,2}$", s):  # last separator is the decimal point
        whole, frac = s[:-3 if s[-3] in ".," else -2], s[-2:].lstrip(".,")
        whole = re.sub(r"[ ,.]", "", whole)
        s = f"{whole}.{frac}"
    else:
        s = re.sub(r"[ ,.]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _money(match: re.Match) -> tuple[float | None, str | None]:
    amount = _to_number(match.group("amt"))
    sym = match.group("sym") or match.group("sym2")
    code = match.group("code") or match.group("code2")
    tail = match.group(0).lower()
    currency = (code or "").upper() or SYMBOLS.get(sym or "") or (
        "KZT" if tail.endswith("тг") else "RUB" if tail.endswith("руб") else None
    )
    return amount, currency


def currencies_in(text: str) -> set[str]:
    """Currency codes the text actually shows next to amounts."""
    text = clean(text)
    found = set()
    for sym, code in SYMBOLS.items():
        if re.search(re.escape(sym) + r"\s?\d|\d\s?" + re.escape(sym), text):
            found.add(code)
    for code in re.findall(rf"\b({CODES})\b", text):
        found.add(code)
    if re.search(r"\d\s?тг\b|\d\s?тенге", text, re.IGNORECASE):
        found.add("KZT")
    if re.search(r"\d\s?руб", text, re.IGNORECASE):
        found.add("RUB")
    return found


GENERIC_SENDERS = re.compile(r"no-?reply|receipt|чек|ofd|офд|billing|notification|support", re.I)


def sender_name(email: Email) -> str:
    name = _store(email)
    return "" if GENERIC_SENDERS.search(name) else name


PLATFORMS = re.compile(
    r"wolt|glovo|yandex|яндекс|uber\s*eats|bolt|chocofood|deliveroo|doordash|kaspi", re.I
)
SUBJECT_STORE = re.compile(
    r"(?:delivered|order from|receipt from|your .*? from|заказ из|заказ в|из ресторана|от)[:\s]+"
    r"(.+?)(?:\s+\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?.*|\s+\d{4}-\d{2}-\d{2}.*)?$",
    re.I,
)


def subject_store(email: Email) -> str:
    """Restaurant or shop named in the subject, e.g. "Your order's delivered: KFC 17.10"."""
    m = SUBJECT_STORE.search(clean(email.subject).strip())
    return m.group(1).strip(" -–—:") if m else ""


def _store(email: Email) -> str:
    name = re.sub(r"<.*?>", "", email.sender).strip().strip('"')
    if name and PLATFORMS.search(name):
        # Delivery apps send the email; the restaurant is usually in the subject.
        return subject_store(email) or name
    if name:
        return name
    domain = re.search(r"@([\w.-]+)", email.sender)
    return domain.group(1).split(".")[-2].title() if domain else ""


INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


def clean(text: str) -> str:
    """Removes invisible direction marks that some receipts put inside amounts."""
    return INVISIBLE.sub("", text).replace("\u00a0", " ")


def details(email: Email) -> ReceiptExtraction:
    """Everything the text rules can find, without deciding if it's a receipt."""
    text = clean(f"{email.subject}\n{email.body}")
    totals = [t for t in (_money(m) for m in TOTAL_RE.finditer(text)) if t[0]]
    total, currency = totals[-1] if totals else (None, None)  # last "Total" is usually final
    tax_match = TAX_RE.search(text)
    order = ORDER_RE.search(text)
    card = CARD_RE.search(text)
    payment = None
    if card:
        payment = card.group(1).title() + (f" ****{card.group(2)}" if card.group(2) else "")
    return ReceiptExtraction(
        is_receipt=bool(totals) and bool(RECEIPT_WORDS.search(text)),
        store=_store(email),
        total=total,
        currency=currency,
        tax=_money(tax_match)[0] if tax_match else None,
        category=keyword_category(email),
        payment_method=payment,
        order_number=order.group(1) if order else None,
    )


def keyword_category(email: Email) -> Category:
    haystack = f"{email.sender} {email.subject} {email.body[:2000]}".lower()
    return next(
        (cat for cat, words in CATEGORY_WORDS.items() if re.search(words, haystack)),
        Category.OTHER,
    )


class RulesExtractor:
    def extract(self, email: Email) -> ReceiptExtraction | None:
        r = details(email)
        return r if r.is_receipt else ReceiptExtraction(is_receipt=False)
