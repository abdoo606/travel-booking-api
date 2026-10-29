"""HTTP tests run only when Flask is installed (CI installs requirements.txt)."""
import os
import unittest

from booking.api import FLASK_AVAILABLE, create_app, seed
from booking.repo import SqliteRepository
from booking.service import BookingService

try:
    import flask  # noqa: F401
except ImportError:  # pragma: no cover
    flask = None


@unittest.skipUnless(FLASK_AVAILABLE, "Flask not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from datetime import date
        self.service = BookingService(SqliteRepository(":memory:"), today=lambda: date(2026, 10, 29))
        seed(self.service)
        self.client = create_app(self.service).test_client()

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_list_trips_with_filter(self):
        response = self.client.get("/trips?origin=CDG")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        # seed() puts three trips out of CDG: two to MAD and one to LIS.
        # "total" is the size of the filtered result set, not the page size.
        self.assertEqual(payload["total"], 3)
        self.assertTrue(all(t["origin"] == "CDG" for t in payload["items"]))
        self.assertIn("seats_left", payload["items"][0])

    def test_filter_narrows_to_a_single_route(self):
        payload = self.client.get("/trips?origin=CDG&destination=MAD").get_json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual({(t["origin"], t["destination"]) for t in payload["items"]},
                         {("CDG", "MAD")})

    def test_total_ignores_pagination_but_items_do_not(self):
        payload = self.client.get("/trips?origin=CDG&limit=1").get_json()
        self.assertEqual(payload["total"], 3)
        self.assertEqual(len(payload["items"]), 1)

    def test_create_booking_returns_201_then_200_on_replay(self):
        trip_id = self.client.get("/trips").get_json()["items"][0]["id"]
        body = {"trip_id": trip_id, "customer_email": "a@b.com", "seats": 1}
        first = self.client.post("/bookings", json=body, headers={"Idempotency-Key": "abc"})
        self.assertEqual(first.status_code, 201)
        second = self.client.post("/bookings", json=body, headers={"Idempotency-Key": "abc"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["id"], second.get_json()["id"])

    def test_validation_error_is_422(self):
        response = self.client.post("/bookings", json={"trip_id": "x"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"], "validation_error")

    def test_unknown_trip_is_404(self):
        response = self.client.post("/bookings", json={"trip_id": "nope",
                                                      "customer_email": "a@b.com"})
        self.assertEqual(response.status_code, 404)

    def test_overbooking_is_409(self):
        items = self.client.get("/trips?destination=MAD").get_json()["items"]
        trip = min(items, key=lambda t: t["seats_left"])
        response = self.client.post("/bookings", json={"trip_id": trip["id"],
                                                       "customer_email": "a@b.com",
                                                       "seats": trip["seats_left"] + 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "seat_unavailable")

    def test_cancel_flow(self):
        trip_id = self.client.get("/trips").get_json()["items"][0]["id"]
        created = self.client.post("/bookings", json={"trip_id": trip_id,
                                                      "customer_email": "a@b.com"}).get_json()
        response = self.client.delete(f"/bookings/{created['id']}")
        self.assertEqual(response.get_json()["status"], "cancelled")


class FlaskAvailabilityTests(unittest.TestCase):
    """Guards against the API suite silently skipping itself on CI."""

    def test_ci_must_install_flask(self):
        if os.environ.get("CI"):
            self.assertTrue(
                FLASK_AVAILABLE,
                "CI installs requirements.txt, so Flask must be importable; "
                "otherwise every HTTP test is skipped and the suite goes green for the wrong reason",
            )
