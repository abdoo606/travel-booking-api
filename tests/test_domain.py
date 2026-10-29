import unittest
from datetime import date

from booking.domain import Trip, parse_date, validate_email, validate_seats
from booking.errors import ValidationError


class DomainTests(unittest.TestCase):
    def test_trip_price_formatting(self):
        trip = Trip("t1", "CDG", "MAD", date(2026, 11, 1), 12900, 4)
        self.assertEqual(trip.price, "129.00 EUR")

    def test_trip_rejects_same_origin_and_destination(self):
        with self.assertRaises(ValidationError):
            Trip("t1", "CDG", "cdg", date(2026, 11, 1), 1000, 4)

    def test_trip_rejects_bad_numbers(self):
        with self.assertRaises(ValidationError):
            Trip("t1", "CDG", "MAD", date(2026, 11, 1), 0, 4)
        with self.assertRaises(ValidationError):
            Trip("t1", "CDG", "MAD", date(2026, 11, 1), 1000, 0)

    def test_email_normalised_and_validated(self):
        self.assertEqual(validate_email("  Someone@Example.COM "), "someone@example.com")
        for bad in ("nope", "a@b", "", "a b@c.com"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_email(bad)

    def test_seats_bounds(self):
        self.assertEqual(validate_seats("3"), 3)
        for bad in (0, 7, "two", None):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_seats(bad)

    def test_parse_date(self):
        self.assertEqual(parse_date("2026-12-01"), date(2026, 12, 1))
        with self.assertRaises(ValidationError):
            parse_date("01/12/2026")
