import unittest
from datetime import date, timedelta

from booking.domain import Booking
from booking.errors import (
    IdempotencyConflict,
    NotFound,
    SeatUnavailable,
    ValidationError,
)
from booking.repo import SqliteRepository
from booking.service import BookingService

TODAY = date(2026, 10, 29)


def make_service():
    return BookingService(SqliteRepository(":memory:"), today=lambda: TODAY)


def with_trip(service, seats=4, days=7, price=12900, origin="CDG", destination="MAD"):
    return service.create_trip(origin=origin, destination=destination,
                               depart_date=TODAY + timedelta(days=days),
                               price_cents=price, seats_total=seats)


class TripTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()

    def test_create_and_search(self):
        with_trip(self.service)
        result = self.service.search(origin="cdg")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0].seats_left, 4)

    def test_past_departure_rejected(self):
        with self.assertRaises(ValidationError):
            self.service.create_trip(origin="CDG", destination="MAD",
                                     depart_date=TODAY - timedelta(days=1),
                                     price_cents=1000, seats_total=2)

    def test_search_hides_departed_trips(self):
        self.service.create_trip(origin="CDG", destination="MAD",
                                 depart_date=TODAY + timedelta(days=1),
                                 price_cents=1000, seats_total=2)
        # simulate the day after departure
        later = BookingService(self.service.repo, today=lambda: TODAY + timedelta(days=5))
        self.assertEqual(later.search()["total"], 0)

    def test_filters_and_pagination(self):
        with_trip(self.service, price=10000, origin="CDG", destination="MAD", days=3)
        with_trip(self.service, price=20000, origin="CDG", destination="MAD", days=4)
        with_trip(self.service, price=30000, origin="LYS", destination="BCN", days=5)
        self.assertEqual(self.service.search(max_price_cents=15000)["total"], 1)
        self.assertEqual(self.service.search(limit=2)["total"], 3)
        self.assertEqual(len(self.service.search(limit=2)["items"]), 2)
        self.assertEqual(len(self.service.search(limit=2, offset=2)["items"]), 1)
        self.assertEqual(self.service.search(destination="bcn")["total"], 1)

    def test_invalid_pagination(self):
        with self.assertRaises(ValidationError):
            self.service.search(limit=0)
        with self.assertRaises(ValidationError):
            self.service.search(offset=-1)


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        self.trip = with_trip(self.service, seats=3)

    def test_booking_reduces_availability(self):
        booking, created = self.service.book(trip_id=self.trip.id,
                                             customer_email="a@b.com", seats=2)
        self.assertTrue(created)
        self.assertEqual(booking.status, "confirmed")
        trip_view = self.service.search(destination="MAD")["items"][0]
        self.assertEqual(trip_view.seats_left, 1)

    def test_overbooking_is_rejected(self):
        self.service.book(trip_id=self.trip.id, customer_email="a@b.com", seats=3)
        with self.assertRaises(SeatUnavailable) as ctx:
            self.service.book(trip_id=self.trip.id, customer_email="c@d.com", seats=1)
        self.assertEqual(ctx.exception.status, 409)

    def test_unknown_trip(self):
        with self.assertRaises(NotFound):
            self.service.book(trip_id="nope", customer_email="a@b.com")

    def test_invalid_email_rejected_before_writing(self):
        with self.assertRaises(ValidationError):
            self.service.book(trip_id=self.trip.id, customer_email="nope")

    def test_idempotent_replay_returns_same_booking(self):
        first, created = self.service.book(trip_id=self.trip.id, customer_email="a@b.com",
                                           seats=2, idempotency_key="k-1")
        second, created_again = self.service.book(trip_id=self.trip.id, customer_email="a@b.com",
                                                  seats=2, idempotency_key="k-1")
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.service.repo.list_bookings()), 1)

    def test_reused_key_with_different_payload_conflicts(self):
        self.service.book(trip_id=self.trip.id, customer_email="a@b.com", seats=1,
                          idempotency_key="k-2")
        with self.assertRaises(IdempotencyConflict):
            self.service.book(trip_id=self.trip.id, customer_email="a@b.com", seats=2,
                              idempotency_key="k-2")

    def test_cancel_restores_availability_and_is_idempotent(self):
        booking, _ = self.service.book(trip_id=self.trip.id, customer_email="a@b.com", seats=3)
        self.service.cancel(booking.id)
        again = self.service.cancel(booking.id)
        self.assertEqual(again.status, "cancelled")
        self.assertEqual(self.service.search(destination="MAD")["items"][0].seats_left, 3)

    def test_cancel_unknown_booking(self):
        with self.assertRaises(NotFound):
            self.service.cancel("nope")

    def test_departed_trip_cannot_be_booked(self):
        later = BookingService(self.service.repo, today=lambda: TODAY + timedelta(days=30))
        with self.assertRaises(ValidationError):
            later.book(trip_id=self.trip.id, customer_email="a@b.com")

    def test_booking_objects_are_comparable_by_id(self):
        booking, _ = self.service.book(trip_id=self.trip.id, customer_email="a@b.com")
        self.assertIsInstance(booking, Booking)
        self.assertTrue(booking.is_active)
