# travel-booking-api

A booking API in four layers — **domain → repository → service → HTTP adapter** —
with SQLite persistence, idempotent booking creation and atomic seat reservation.

Core: zero dependencies. HTTP adapter: Flask (`requirements.txt`).

## Why this shape

The interesting decisions in a booking system are not in the routes:

- **Overbooking is a race.** Capacity is checked and the row inserted inside one
  `BEGIN IMMEDIATE` transaction; the pre-check in the service is a fast path,
  the `OverflowError` path is the truth.
- **Retries are the client's fault, not the customer's.** `Idempotency-Key`
  replays the original booking; the same key with a different payload is a
  `409 idempotency_conflict`, not a second charge.
- **Cancellation is idempotent** and availability is derived from confirmed
  bookings, never from a mutable counter that can drift.
- **Errors own their status code** (`ValidationError` → 422, `NotFound` → 404,
  `SeatUnavailable` → 409), so the adapter has no `if/else` ladder.

## Run

```bash
python -m unittest discover -s tests -v          # core tests, no Flask needed
pip install -r requirements.txt                  # optional: adds the HTTP tests
python -m booking.api                            # serves on 0.0.0.0:8000
```

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/trips?origin=&destination=&max_price_cents=&limit=&offset=` | paginated, includes `seats_left` |
| POST | `/bookings` | `Idempotency-Key` header; 201 created / 200 replayed |
| GET | `/bookings/<id>` | |
| DELETE | `/bookings/<id>` | idempotent cancel |
| GET | `/health` | |

## Tests

34 tests: domain invariants, repository transactions (including the
`sqlite3.IntegrityError` path for duplicate idempotency keys), service rules,
and the HTTP contract (skipped automatically when Flask is absent).
