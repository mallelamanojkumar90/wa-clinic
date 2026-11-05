"""Clinic tools re-export for backwards compatibility. Implementation moved to app.core.tools."""
from app.core.tools import (
    get_free_slots,
    book_slot,
    cancel_booking,
    seed_slots_if_needed,
    _get_tz,
    _to_clinic_tz,
    TOOL_DEFINITIONS,
)

__all__ = [
    "get_free_slots",
    "book_slot",
    "cancel_booking",
    "seed_slots_if_needed",
    "_get_tz",
    "_to_clinic_tz",
    "TOOL_DEFINITIONS",
]
