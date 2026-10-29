"""SQLite persistence. The service never sees SQL; the API never sees a cursor."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import date, datetime
from typing import Iterable

from .domain import Booking, Trip

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id           TEXT PRIMARY KEY,
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_date  TEXT NOT NULL,
    price_cents  INTEGER NOT NULL CHECK (price_cents > 0),
    seats_total  INTEGER NOT NULL CHECK (seats_total > 0)
);
CREATE INDEX IF NOT EXISTS idx_trips_route ON trips (origin, destination, depart_date);

CREATE TABLE IF NOT EXISTS bookings (
    id          TEXT PRIMARY KEY,
    trip_id     TEXT NOT NULL REFERENCES trips(id),
    customer_email TEXT NOT NULL,
    seats       INTEGER NOT NULL CHECK (seats BETWEEN 1 AND 6),
    status      TEXT NOT NULL DEFAULT 'confirmed',
    idempotency_key TEXT UNIQUE,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_trip ON bookings (trip_id, status);
"""


class SqliteRepository:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- trips -------------------------------------------------------------
    def add_trip(self, trip: Trip) -> Trip:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO trips (id, origin, destination, depart_date, price_cents, seats_total)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (trip.id, trip.origin.upper(), trip.destination.upper(),
                 trip.depart_date.isoformat(), trip.price_cents, trip.seats_total),
            )
        return trip

    def get_trip(self, trip_id: str) -> Trip | None:
        row = self._conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return self._to_trip(row) if row else None

    def search_trips(self, origin: str | None, destination: str | None,
                     max_price_cents: int | None, not_before: date,
                     limit: int, offset: int) -> tuple[list[Trip], int]:
        where = ["depart_date >= ?"]
        params: list = [not_before.isoformat()]
        if origin:
            where.append("origin = ?")
            params.append(origin.upper())
        if destination:
            where.append("destination = ?")
            params.append(destination.upper())
        if max_price_cents is not None:
            where.append("price_cents <= ?")
            params.append(max_price_cents)
        clause = " AND ".join(where)
        total = self._conn.execute(f"SELECT COUNT(*) AS n FROM trips WHERE {clause}",
                                   params).fetchone()["n"]
        rows = self._conn.execute(
            f"SELECT * FROM trips WHERE {clause} ORDER BY depart_date, price_cents LIMIT ? OFFSET ?",
            [*params, limit, offset]).fetchall()
        return [self._to_trip(r) for r in rows], total

    # -- bookings ----------------------------------------------------------
    def seats_taken(self, trip_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(seats), 0) AS n FROM bookings"
            " WHERE trip_id = ? AND status = 'confirmed'", (trip_id,)).fetchone()
        return int(row["n"])

    def reserve_seats(self, trip_id: str, customer_email: str, seats: int,
                      idempotency_key: str | None) -> Booking:
        """Insert a booking only if capacity still allows it, atomically."""
        booking = Booking(id=uuid.uuid4().hex[:12], trip_id=trip_id, customer_email=customer_email,
                          seats=seats, idempotency_key=idempotency_key)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                trip = self._conn.execute("SELECT seats_total FROM trips WHERE id = ?",
                                          (trip_id,)).fetchone()
                taken = self._conn.execute(
                    "SELECT COALESCE(SUM(seats), 0) AS n FROM bookings"
                    " WHERE trip_id = ? AND status = 'confirmed'", (trip_id,)).fetchone()["n"]
                if trip is None:
                    raise ValueError("trip disappeared")
                if int(taken) + seats > int(trip["seats_total"]):
                    raise OverflowError(int(trip["seats_total"]) - int(taken))
                self._conn.execute(
                    "INSERT INTO bookings (id, trip_id, customer_email, seats, status,"
                    " idempotency_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (booking.id, trip_id, customer_email, seats, booking.status,
                     idempotency_key, booking.created_at.isoformat()),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return booking

    def get_booking(self, booking_id: str) -> Booking | None:
        row = self._conn.execute("SELECT * FROM bookings WHERE id = ?",
                                 (booking_id,)).fetchone()
        return self._to_booking(row) if row else None

    def get_by_idempotency_key(self, key: str) -> Booking | None:
        row = self._conn.execute("SELECT * FROM bookings WHERE idempotency_key = ?",
                                 (key,)).fetchone()
        return self._to_booking(row) if row else None

    def set_status(self, booking_id: str, status: str) -> Booking | None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE bookings SET status = ? WHERE id = ?",
                               (status, booking_id))
        return self.get_booking(booking_id)

    def list_bookings(self, customer_email: str | None = None) -> list[Booking]:
        if customer_email:
            rows = self._conn.execute(
                "SELECT * FROM bookings WHERE customer_email = ? ORDER BY created_at DESC",
                (customer_email,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM bookings ORDER BY created_at DESC").fetchall()
        return [self._to_booking(r) for r in rows]

    # -- mapping -----------------------------------------------------------
    @staticmethod
    def _to_trip(row: sqlite3.Row) -> Trip:
        return Trip(id=row["id"], origin=row["origin"], destination=row["destination"],
                    depart_date=date.fromisoformat(row["depart_date"]),
                    price_cents=row["price_cents"], seats_total=row["seats_total"])

    @staticmethod
    def _to_booking(row: sqlite3.Row) -> Booking:
        return Booking(id=row["id"], trip_id=row["trip_id"], customer_email=row["customer_email"],
                       seats=row["seats"], status=row["status"],
                       idempotency_key=row["idempotency_key"],
                       created_at=datetime.fromisoformat(row["created_at"]))

    def close(self) -> None:
        self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:  # for tests that need raw SQL
        return self._conn
