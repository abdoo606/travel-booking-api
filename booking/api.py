"""Thin HTTP adapter. Flask is imported lazily so the core stays testable without it."""
from __future__ import annotations

from .errors import AppError, ValidationError
from .service import BookingService

try:  # pragma: no cover - import guard
    from flask import Flask, jsonify, request
    FLASK_AVAILABLE = True
except ImportError:  # pragma: no cover
    Flask = None  # type: ignore[assignment]
    FLASK_AVAILABLE = False


def create_app(service: BookingService):
    if not FLASK_AVAILABLE:  # pragma: no cover
        raise RuntimeError("Flask is not installed: pip install -r requirements.txt")

    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    @app.errorhandler(AppError)
    def _handle_app_error(exc: AppError):
        return jsonify(exc.to_dict()), exc.status

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/trips")
    def list_trips():
        result = service.search(
            origin=request.args.get("origin"),
            destination=request.args.get("destination"),
            max_price_cents=request.args.get("max_price_cents", type=int),
            limit=request.args.get("limit", default=20, type=int),
            offset=request.args.get("offset", default=0, type=int),
        )
        return jsonify({"items": [v.as_dict() for v in result["items"]],
                        "total": result["total"], "limit": result["limit"],
                        "offset": result["offset"]})

    @app.post("/bookings")
    def create_booking():
        body = request.get_json(silent=True) or {}
        for field in ("trip_id", "customer_email"):
            if field not in body:
                raise ValidationError(f"{field} is required", field=field)
        booking, created = service.book(
            trip_id=body["trip_id"],
            customer_email=body["customer_email"],
            seats=body.get("seats", 1),
            idempotency_key=request.headers.get("Idempotency-Key") or body.get("idempotency_key"),
        )
        payload = {"id": booking.id, "trip_id": booking.trip_id,
                   "customer_email": booking.customer_email, "seats": booking.seats,
                   "status": booking.status}
        return jsonify(payload), (201 if created else 200)

    @app.get("/bookings/<booking_id>")
    def get_booking(booking_id: str):
        b = service.get_booking(booking_id)
        return jsonify({"id": b.id, "trip_id": b.trip_id, "customer_email": b.customer_email,
                        "seats": b.seats, "status": b.status})

    @app.delete("/bookings/<booking_id>")
    def cancel_booking(booking_id: str):
        b = service.cancel(booking_id)
        return jsonify({"id": b.id, "status": b.status})

    return app


def seed(service: BookingService) -> None:
    """Small demo dataset used by `python -m booking.demo` and the API tests."""
    from datetime import date, timedelta

    start = service._today() + timedelta(days=7)
    rows = [
        ("CDG", "MAD", 0, 12900, 4),
        ("CDG", "MAD", 1, 9900, 2),
        ("LYS", "BCN", 2, 14900, 6),
        ("CDG", "LIS", 3, 17500, 3),
    ]
    for origin, destination, offset, price, seats in rows:
        service.create_trip(origin=origin, destination=destination,
                            depart_date=start + timedelta(days=offset),
                            price_cents=price, seats_total=seats)


def main() -> int:  # pragma: no cover
    from .repo import SqliteRepository

    service = BookingService(SqliteRepository("bookings.db"))
    seed(service)
    create_app(service).run(host="0.0.0.0", port=8000)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
