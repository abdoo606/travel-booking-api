"""Application errors carry their own HTTP status, so the adapter stays dumb."""
from __future__ import annotations


class AppError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        payload = {"error": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(AppError):
    status = 422
    code = "validation_error"


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"


class SeatUnavailable(Conflict):
    code = "seat_unavailable"


class IdempotencyConflict(Conflict):
    code = "idempotency_conflict"
