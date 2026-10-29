#!/usr/bin/env python3
"""Fix the failing CI job in travel-booking-api.

What was wrong
--------------
The HTTP test called ``/trips?origin=CDG`` and expected ``total == 2``, but
``seed()`` creates three trips departing from CDG (two to MAD, one to LIS).
The API was right; the assertion was stale and only surfaced in CI, because
Flask is installed there and the seven HTTP tests actually run — locally they
were skipped, so the suite looked green while never exercising the endpoint.

What this script does
---------------------
1. Replaces ``tests/test_api.py`` with a version whose assertions match the
   seeded data, plus a guard that fails the build if Flask is ever missing on
   CI (which would make the whole HTTP suite skip silently again).
2. Updates the test count and the skip note in ``README.md``.
3. Runs the test suite.
4. Commits and pushes to ``main`` so CI re-runs.

Usage (from anywhere):
    python fix-travel-booking-api.py
    python fix-travel-booking-api.py --no-push     # fix + commit only
    python fix-travel-booking-api.py --dir C:\\path\\to\\travel-booking-api
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

TEST_API = '''"""HTTP tests run only when Flask is installed (CI installs requirements.txt)."""
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
'''

README_OLD = """34 tests: domain invariants, repository transactions (including the
`sqlite3.IntegrityError` path for duplicate idempotency keys), service rules,
and the HTTP contract (skipped automatically when Flask is absent)."""

README_NEW = """37 tests: domain invariants, repository transactions (including the
`sqlite3.IntegrityError` path for duplicate idempotency keys), service rules,
and the HTTP contract. The HTTP tests are skipped automatically when Flask is
absent — CI installs `requirements.txt`, and a dedicated guard fails the build
if they would ever be skipped there, so a green badge always means every test
actually ran."""

COMMIT_MSG = """Fix stale filter assertion and guard against silently skipped HTTP tests

The seeded dataset has three trips out of CDG (two to MAD, one to LIS), so
/trips?origin=CDG legitimately returns total=3; the test asserted 2. Replaced
it with three precise assertions covering the filter, route narrowing and
total-vs-page-size, and added a guard that fails the CI build if Flask is
missing there, since the HTTP suite would otherwise skip and the build would
go green without running it."""


def find_repo(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "booking" / "api.py").is_file() and (candidate / "tests").is_dir():
            return candidate
    raise SystemExit(
        "Could not find the travel-booking-api folder here.\n"
        "Run this script from inside it, or pass --dir <path>."
    )


def run(cmd, cwd: Path, check: bool = False) -> int:
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd))
    if check and result.returncode != 0:
        raise SystemExit(f"Command failed with exit code {result.returncode}")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=".", help="path to the travel-booking-api checkout")
    parser.add_argument("--no-push", action="store_true", help="commit but do not push")
    args = parser.parse_args()

    repo = find_repo(Path(args.dir).resolve())
    print(f"repo: {repo}\n")

    test_file = repo / "tests" / "test_api.py"
    current = test_file.read_text(encoding="utf-8") if test_file.is_file() else ""
    if current.strip() == TEST_API.strip():
        print("tests/test_api.py already fixed — skipping the rewrite.")
    else:
        test_file.write_text(TEST_API, encoding="utf-8", newline="\n")
        print("rewrote tests/test_api.py")

    readme = repo / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        if README_OLD in text:
            readme.write_text(text.replace(README_OLD, README_NEW), encoding="utf-8", newline="\n")
            print("updated README.md test counts")
        elif README_NEW in text:
            print("README.md already updated — skipping.")
        else:
            print("! README.md did not match the expected text; left untouched.")

    print("\n--- tests (without Flask: HTTP tests skip, that is expected) ---")
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], repo)

    print("\n--- tests (CI=true, exactly what GitHub Actions runs) ---")
    env = dict(os.environ, CI="true")
    print("$ CI=true python -m unittest discover -s tests")
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                            cwd=str(repo), env=env)
    print(f"exit code: {result.returncode}")

    print("\n--- git ---")
    run(["git", "add", "-A"], repo)
    if run(["git", "diff", "--cached", "--quiet"], repo) == 0:
        print("nothing to commit — the repo is already fixed.")
    else:
        run(["git", "commit", "-m", COMMIT_MSG], repo)
        if args.no_push:
            print("committed, not pushed (--no-push).")
        else:
            code = run(["git", "push", "origin", "main"], repo)
            if code != 0:
                print("\n! push failed. Run this once, then retry:\n    gh auth setup-git")

    print("\nDone. Watch the re-run with:\n"
          "    gh run list --repo abdoo606/travel-booking-api --limit 3\n"
          "    gh run watch --repo abdoo606/travel-booking-api")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
