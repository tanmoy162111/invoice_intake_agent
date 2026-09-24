"""Closed vocabularies shared by the DB layer, pipeline and API (lower_snake_case values)."""

from enum import StrEnum


class InvoiceStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    CHECKING = "checking"
    CLEARED = "cleared"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPORTED = "exported"
    FAILED = "failed"


class Route(StrEnum):
    STRAIGHT_THROUGH = "straight_through"
    REVIEW = "review"


class DocQuality(StrEnum):
    CLEAN = "clean"
    SCANNED = "scanned"
    PHOTO = "photo"
    UNKNOWN = "unknown"


class ActorType(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    USER = "user"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CORRECT_FIELD = "correct_field"
    DISMISS_EXCEPTION = "dismiss_exception"
    REQUEST_INFO = "request_info"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
