"""Business rules. This is the layer the unit tests spend their time on."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Callable

from .domain import Booking, Trip, parse_date, validate_email, validate_seats
from .errors import IdempotencyConflict, NotFound, SeatUnavailable, ValidationError
from .repo import SqliteRepository


@dataclass
class TripView:
    """A trip plus what is left, because that is what a client needs to decide."""

    trip: Trip
    seats_left: int

    def as_dict(self) -> dict:
        return {
            "id": self.trip.id,
            "origin": self.trip.origin,
            "destination": self.trip.destination,
            "depart_date": self.trip.depart_date.isoformat(),
            "price_cents": self.trip.price_cents,
            "price": self.trip.price,
            "seats_total": self.trip.seats_total,
            "seats_left": self.seats_left,
        }


class BookingService:
    def __init__(self, repo: SqliteRepository, today: Callable[[], date] | None = None):
        self.repo = repo
        self._today = today or date.today

    # -- trips -------------------------------------------------------------
    def create_trip(self, *, origin: str, destination: str, depart_date: str | date,
                    price_cents: int, seats_total: int, trip_id: str | None = None) -> Trip:
        parsed = parse_date(depart_date)
        if parsed < self._today():
            raise ValidationError("depart_date is in the past", field="depart_date")
        trip = Trip(id=trip_id or uuid.uuid4().hex[:10], origin=origin.strip(),
                    destination=destination.strip(), depart_date=parsed,
                    price_cents=int(price_cents), seats_total=int(seats_total))
        return self.repo.add_trip(trip)

    def search(self, *, origin: str | None = None, destination: str | None = None,
               max_price_cents: int | None = None, limit: int = 20,
               offset: int = 0) -> dict:
        if limit < 1 or limit > 100:
            raise ValidationError("limit must be between 1 and 100", field="limit")
        if offset < 0:
            raise ValidationError("offset must be >= 0", field="offset")
        trips, total = self.repo.search_trips(origin, destination, max_price_cents,
                                              self._today(), limit, offset)
        items = [TripView(t, t.seats_total - self.repo.seats_taken(t.id)) for t in trips]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # -- bookings ----------------------------------------------------------
    def book(self, *, trip_id: str, customer_email: str, seats: int = 1,
             idempotency_key: str | None = None) -> tuple[Booking, bool]:
        """Return (booking, created). A repeated key replays instead of double-charging."""
        email = validate_email(customer_email)
        seats = validate_seats(seats)

        if idempotency_key:
            existing = self.repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                same_request = (existing.trip_id == trip_id
                                and existing.customer_email == email
                                and existing.seats == seats)
                if not same_request:
                    raise IdempotencyConflict(
                        "this idempotency key was used with a different payload")
                return existing, False

        trip = self.repo.get_trip(trip_id)
        if trip is None:
            raise NotFound(f"trip {trip_id} not found")
        if trip.depart_date < self._today():
            raise ValidationError("trip has already departed", field="trip_id")

        left = trip.seats_total - self.repo.seats_taken(trip_id)
        if seats > left:
            raise SeatUnavailable(f"only {left} seat(s) left on trip {trip_id}",
                                  seats_left=left)
        try:
            booking = self.repo.reserve_seats(trip_id, email, seats, idempotency_key)
        except OverflowError as exc:  # lost a race between check and insert
            raise SeatUnavailable(f"only {exc.args[0]} seat(s) left on trip {trip_id}") from exc
        return booking, True

    def cancel(self, booking_id: str) -> Booking:
        booking = self.repo.get_booking(booking_id)
        if booking is None:
            raise NotFound(f"booking {booking_id} not found")
        if booking.status == "cancelled":
            return booking  # cancelling twice is not an error
        updated = self.repo.set_status(booking_id, "cancelled")
        assert updated is not None
        return updated

    def get_booking(self, booking_id: str) -> Booking:
        booking = self.repo.get_booking(booking_id)
        if booking is None:
            raise NotFound(f"booking {booking_id} not found")
        return booking
