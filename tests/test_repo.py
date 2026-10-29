import sqlite3
import unittest
from datetime import date, datetime

from booking.domain import Booking, Trip
from booking.repo import SqliteRepository


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.repo = SqliteRepository(":memory:")
        self.trip = self.repo.add_trip(Trip("t1", "CDG", "MAD", date(2026, 11, 5), 9900, 2))

    def tearDown(self):
        self.repo.close()

    def test_add_and_get_trip(self):
        fetched = self.repo.get_trip("t1")
        self.assertEqual(fetched, self.trip)

    def test_missing_trip_is_none(self):
        self.assertIsNone(self.repo.get_trip("ghost"))

    def test_reserve_rejects_overflow_atomically(self):
        self.repo.reserve_seats("t1", "a@b.com", 2, None)
        with self.assertRaises(OverflowError):
            self.repo.reserve_seats("t1", "c@d.com", 1, None)
        self.assertEqual(self.repo.seats_taken("t1"), 2)
        self.assertEqual(len(self.repo.list_bookings()), 1)

    def test_unique_idempotency_key_enforced_by_database(self):
        self.repo.reserve_seats("t1", "a@b.com", 1, "same-key")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.reserve_seats("t1", "a@b.com", 1, "same-key")

    def test_status_change(self):
        booking = self.repo.reserve_seats("t1", "a@b.com", 1, None)
        cancelled = self.repo.set_status(booking.id, "cancelled")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self.repo.seats_taken("t1"), 0)

    def test_mapping_roundtrip(self):
        booking = self.repo.reserve_seats("t1", "a@b.com", 1, "k")
        fetched = self.repo.get_booking(booking.id)
        self.assertIsInstance(fetched, Booking)
        self.assertIsInstance(fetched.created_at, datetime)
        self.assertEqual(fetched.idempotency_key, "k")
