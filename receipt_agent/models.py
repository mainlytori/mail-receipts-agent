"""Shared data types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


@dataclass
class Email:
    """One email, in the same shape whichever inbox it came from."""

    provider: str  # "gmail" or "outlook"
    account: str  # the mailbox address
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    body: str  # plain text
    link: str  # opens the email in the browser

    @property
    def key(self) -> str:
        """Unique id across all inboxes, used to skip emails already handled."""
        return f"{self.provider}:{self.account}:{self.message_id}"


class Category(str, Enum):
    GROCERIES = "Groceries"
    FOOD_DRINKS = "Food & Drinks"
    SHOPPING = "Shopping"
    TRANSPORT = "Transport"
    BILLS = "Bills"
    SUBSCRIPTIONS = "Subscriptions"
    HEALTH = "Health"
    TRAVEL = "Travel"
    ENTERTAINMENT = "Entertainment"
    OTHER = "Other"


class ReceiptExtraction(BaseModel):
    """What the AI returns for one email."""

    is_receipt: bool = Field(
        description="True only if the email confirms money was actually paid "
        "(receipt, order confirmation, invoice paid, payment confirmation). "
        "False for ads, newsletters, shipping updates without prices, refunds, or unpaid bills."
    )
    purchase_date: str | None = Field(
        default=None, description="Date of purchase as YYYY-MM-DD, if stated."
    )
    store: str | None = Field(default=None, description="Merchant or service name.")
    total: float | None = Field(default=None, description="Total amount paid, as a number.")
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. USD, EUR, KZT.")
    tax: float | None = Field(default=None, description="Tax / VAT amount, if shown.")
    category: Category | None = Field(default=None)
    payment_method: str | None = Field(
        default=None, description="e.g. 'Visa ****1234', 'PayPal', 'Apple Pay'."
    )
    items: list[str] = Field(
        default_factory=list, description="Short names of purchased items, with quantity if >1."
    )
    order_number: str | None = Field(default=None)


@dataclass
class ReceiptRow:
    """One row in the expense table."""

    date: str
    store: str
    type: str
    category: str
    total: float | None
    currency: str
    payment_method: str
    items: str
    order_number: str
    account: str
    email_subject: str
    email_key: str

    HEADERS = [
        "Date",
        "Store",
        "Type",
        "Category",
        "Total",
        "Currency",
        "Payment method",
        "Items",
        "Order number",
        "Email account",
        "Email subject",
        "Email ID",
    ]

    def values(self) -> list:
        return [
            self.date,
            self.store,
            self.type,
            self.category,
            self.total,
            self.currency,
            self.payment_method,
            self.items,
            self.order_number,
            self.account,
            self.email_subject,
            self.email_key,
        ]

    @classmethod
    def from_extraction(cls, email: Email, r: ReceiptExtraction) -> "ReceiptRow":
        category = (r.category or Category.OTHER).value
        store = r.store or ""
        items = "; ".join(r.items)
        return cls(
            date=r.purchase_date or email.received_at.date().isoformat(),
            store=store,
            type=type_for(category, f"{store} {items} {email.subject}"),
            category=category,
            total=r.total,
            currency=(r.currency or "").upper(),
            payment_method=r.payment_method or "",
            items=items,
            order_number=r.order_number or "",
            account=email.account,
            email_subject=email.subject,
            email_key=email.key,
        )


def migrate_row(header: list[str], row: list) -> list:
    """Turns a row written with an older column layout into the current one."""
    old = {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}
    if not old.get("Type"):
        old["Type"] = type_for(
            str(old.get("Category", "")),
            f"{old.get('Store', '')} {old.get('Items', '')} {old.get('Email subject', '')}",
        )
    return [old.get(h, "") for h in ReceiptRow.HEADERS]


# The general "Type" of a spend, broader than the category.
STUDY_WORDS = re.compile(
    r"anthropic|claude|chatgpt|openai|gemini|perplexity|duolingo|coursera|udemy|skillbox"
    r"|edx|khan|quizlet|grammarly|notion|\bbooks?\b|книг|course|курс|school|школ|univer|универ"
    r"|tuition|\bexams?\b|ielts|toefl",
    re.IGNORECASE,
)
TYPE_BY_CATEGORY = {
    "Groceries": "Food",
    "Food & Drinks": "Food",
    "Shopping": "Shopping",
    "Transport": "Transport",
    "Bills": "Home & Bills",
    "Subscriptions": "Subscriptions",
    "Health": "Health & Beauty",
    "Travel": "Travel",
    "Entertainment": "Fun",
    "Other": "Other",
}
TYPES = ["Food", "Study", "Shopping", "Transport", "Home & Bills", "Subscriptions",
         "Health & Beauty", "Travel", "Fun", "Other"]


def type_for(category: str, text: str) -> str:
    if STUDY_WORDS.search(text or ""):
        return "Study"
    return TYPE_BY_CATEGORY.get(category, "Other")
