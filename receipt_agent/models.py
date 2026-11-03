"""Shared data types."""

from __future__ import annotations

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
    total: float | None
    currency: str
    tax: float | None
    category: str
    payment_method: str
    items: str
    order_number: str
    account: str
    email_subject: str
    email_link: str
    email_key: str

    HEADERS = [
        "Date",
        "Store",
        "Total",
        "Currency",
        "Tax",
        "Category",
        "Payment method",
        "Items",
        "Order number",
        "Email account",
        "Email subject",
        "Link to email",
        "Email ID",
    ]

    def values(self) -> list:
        return [
            self.date,
            self.store,
            self.total,
            self.currency,
            self.tax,
            self.category,
            self.payment_method,
            self.items,
            self.order_number,
            self.account,
            self.email_subject,
            self.email_link,
            self.email_key,
        ]

    @classmethod
    def from_extraction(cls, email: Email, r: ReceiptExtraction) -> "ReceiptRow":
        return cls(
            date=r.purchase_date or email.received_at.date().isoformat(),
            store=r.store or "",
            total=r.total,
            currency=(r.currency or "").upper(),
            tax=r.tax,
            category=(r.category or Category.OTHER).value,
            payment_method=r.payment_method or "",
            items="; ".join(r.items),
            order_number=r.order_number or "",
            account=email.account,
            email_subject=email.subject,
            email_link=email.link,
            email_key=email.key,
        )
