"""Scheduler re-export for backwards compatibility. Implementation moved to app.core.scheduler."""
from app.core.scheduler import (
    scheduler,
    _get_tz,
    _to_clinic_tz,
    check_and_send_reminders,
    release_expired_holds,
    sync_and_clean_orphan_calendar_events,
    start_scheduler,
    stop_scheduler,
)

__all__ = [
    "scheduler",
    "_get_tz",
    "_to_clinic_tz",
    "check_and_send_reminders",
    "release_expired_holds",
    "sync_and_clean_orphan_calendar_events",
    "start_scheduler",
    "stop_scheduler",
]
