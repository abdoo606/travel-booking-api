"""Domain objects and their invariants. No I/O, no framework."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .errors import ValidationError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
MAX_SEATS_PER_BOOKING = 6
CURRENCY = "EUR"


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def validate_email(value: str) -> str:
    value = (value or "").strip()
    if not EMAIL_RE.match(value):
        raise ValidationError("invalid email address", field="customer_email")
    return value.lower()


def validate_seats(value: int | str) -> int:
    try:
        seats = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("seats must be an integer", field="seats") from exc
    if not 1 <= seats <= MAX_SEATS_PER_BOOKING:
        raise ValidationError(f"seats must be between 1 and {MAX_SEATS_PER_BOOKING}",
                              field="seats")
    return seats


@dataclass(frozen=True)
class Trip:
    id: str
    origin: str
    destination: str
    depart_date: date
    price_cents: int
    seats_total: int

    def __post_init__(self) -> None:
        if not self.id:
            raise ValidationError("trip id is required")
        if self.origin.upper() == self.destination.upper():
            raise ValidationError("origin and destination must differ")
        if self.price_cents <= 0:
            raise ValidationError("price_cents must be positive")
        if self.seats_total <= 0:
            raise ValidationError("seats_total must be positive")

    @property
    def price(self) -> str:
        return f"{self.price_cents / 100:.2f} {CURRENCY}"


@dataclass(frozen=True)
class Booking:
    id: str
    trip_id: str
    customer_email: str
    seats: int
    status: str = "confirmed"          # confirmed | cancelled
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_active(self) -> bool:
        return self.status == "confirmed"
