"""Receptionist tools callable by the LLM agent: get_free_slots, book_slot, cancel_booking.

Also holds the slot helpers shared by every channel (WhatsApp, voice, dashboard):
- available_slot_rows: the single source of truth for what can be offered and booked
- match_requested_slot: turns "Sat 14 Nov 11:00 AM", "tomorrow at 6 pm" or an ISO
  string into one specific slot, or None when the request is ambiguous
- claim_slot: atomically takes a slot so two patients can never book the same one
"""
import logging
import re
from datetime import date, datetime, time as dt_time, timedelta
from typing import Iterable, List, Optional, Tuple
import pytz
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import db_session
from app.core.models import Appointment
from app.core.calendar_service import calendar_service, CLINIC_SLOT_HOURS, CLINIC_DAYS_OF_WEEK

logger = logging.getLogger(__name__)

BOOKING_LEAD_TIME = timedelta(hours=1)
OPEN_STATUSES = ("free", "cancelled")

def _get_tz():
    return pytz.timezone(settings.CLINIC_TIMEZONE)

def _to_clinic_tz(d):
    """Convert to clinic time. Naive values are clinic wall-clock time (how SQLite stores them)."""
    if d is None:
        return None
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if d.tzinfo is None:
        d = _get_tz().localize(d)
    return d.astimezone(_get_tz())

def _slot_iso(d) -> str:
    return _to_clinic_tz(d).strftime("%Y-%m-%dT%H:%M:%S")

# -----------------------------------------------------------------------------
# Slot state helpers
# -----------------------------------------------------------------------------

def cleared_slot_fields() -> dict:
    """Every per-booking field, reset. Applied whenever a slot changes hands so a new
    patient never inherits the previous patient's reminder flags or payment link."""
    return {
        "patient_name": "",
        "phone": "",
        "notes": None,
        "google_event_id": None,
        "reminder_24h_sent": False,
        "reminder_2h_sent": False,
        "payment_status": "none",
        "payment_link_id": None,
        "payment_link_url": None,
        "payment_amount": None,
        "hold_expires_at": None,
    }

def claim_slot(session: Session, appointment_id: int, allowed_statuses: Iterable[str] = OPEN_STATUSES, **fields) -> bool:
    """Atomically assign a slot. Returns False if someone else took it first.

    Uses a conditional UPDATE (... WHERE status IN allowed_statuses) so concurrent
    bookings of the same slot cannot both succeed.
    """
    values = {**cleared_slot_fields(), **fields}
    claimed = session.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.status.in_(list(allowed_statuses)),
    ).update(values, synchronize_session=False)
    session.flush()
    session.expire_all()
    return claimed == 1

def seed_slots_if_needed():
    """Ensure upcoming slots exist in the database."""
    tz = _get_tz()
    now = datetime.now(tz)
    with db_session() as session:
        # Clean up unbooked slots in the past
        session.query(Appointment).filter(
            Appointment.status.in_(["free", "cancelled"]),
            Appointment.slot < now
        ).delete(synchronize_session=False)

        # Fetch all existing slots to avoid SQLite datetime string comparison mismatches
        existing_set = set()
        for (slot_value,) in session.query(Appointment.slot).all():
            try:
                existing_set.add(_to_clinic_tz(slot_value).strftime("%Y-%m-%d %H:%M"))
            except Exception:
                pass

        # Seed today + next 3 days, clinic days only (same window the calendar check uses)
        for day in range(0, 4):
            target_date = (now + timedelta(days=day)).date()
            if target_date.weekday() not in CLINIC_DAYS_OF_WEEK:
                continue
            for hour in CLINIC_SLOT_HOURS:
                slot_dt = tz.localize(datetime.combine(target_date, dt_time(hour, 0, 0)))
                if slot_dt > now + BOOKING_LEAD_TIME:
                    key = slot_dt.strftime("%Y-%m-%d %H:%M")
                    if key not in existing_set:
                        session.add(Appointment(patient_name="", phone="", slot=slot_dt, status="free"))

def available_slot_rows(session: Session, now: Optional[datetime] = None) -> List[Appointment]:
    """Open slots past the booking lead time, minus anything busy on Google Calendar.

    Both offering and booking use this, so a patient can only book what they were offered.
    """
    now = now or datetime.now(_get_tz())
    rows = session.query(Appointment).filter(
        Appointment.status.in_(list(OPEN_STATUSES)),
        Appointment.slot > now + BOOKING_LEAD_TIME
    ).order_by(Appointment.slot).all()

    if calendar_service.is_available:
        cal_slots = calendar_service.get_free_slots(days_ahead=3)
        if cal_slots is not None:
            free_isos = {iso for iso, _ in cal_slots}
            rows = [r for r in rows if _slot_iso(r.slot) in free_isos]
        else:
            logger.warning("Google Calendar unavailable; offering database slots only.")
    return rows

def _format_slot(d) -> str:
    local_d = _to_clinic_tz(d)
    return f'{local_d.strftime("%Y-%m-%dT%H:%M:%S")} ({local_d.strftime("%a %d %b %I:%M %p")})'

def get_free_slots() -> List[str]:
    """Available appointment slots as 'YYYY-MM-DDTHH:MM:SS (Sat 14 Nov 11:00 AM)' strings."""
    seed_slots_if_needed()
    with db_session() as session:
        return [_format_slot(r.slot) for r in available_slot_rows(session)]

# -----------------------------------------------------------------------------
# Slot request parsing
# -----------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
_MONTH_ALT = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[t ](\d{1,2}):(\d{2}))?")
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})[a-z]*\b")
_MONTH_DAY_RE = re.compile(rf"\b({_MONTH_ALT})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?\s?m\b\.?|p\.?\s?m\b\.?)?(?![\d:])")
_TODAY_WORDS = ("today", "aaj", "eeroju", "ee roju", "ivala", "ivvala")
_TOMORROW_WORDS = ("tomorrow", "kal", "repu")

def _resolve_month_day(month: int, day: int, today: date) -> Optional[date]:
    try:
        d = date(today.year, month, day)
        if d < today - timedelta(days=7):  # e.g. "2 Jan" asked in late December
            d = date(today.year + 1, month, day)
        return d
    except ValueError:
        return None

def _parse_slot_request(text: str, now: datetime) -> Tuple[Optional[date], Optional[int], Optional[int], int]:
    """Return (date, weekday, hour, minute) parsed from free text; None where not stated."""
    t = (text or "").lower()
    today = now.date()
    req_date: Optional[date] = None
    weekday: Optional[int] = None
    hour: Optional[int] = None
    minute = 0

    m = _ISO_RE.search(t)
    if m:
        try:
            req_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            req_date = None
        if m.group(4) is not None:
            hour, minute = int(m.group(4)), int(m.group(5))
        t = t[:m.start()] + " " + t[m.end():]

    if req_date is None:
        m = _DAY_MONTH_RE.search(t)
        if m:
            req_date = _resolve_month_day(_MONTHS[m.group(2)], int(m.group(1)), today)
        else:
            m = _MONTH_DAY_RE.search(t)
            if m:
                req_date = _resolve_month_day(_MONTHS[m.group(1)], int(m.group(2)), today)
        if m:
            t = t[:m.start()] + " " + t[m.end():]

    if req_date is None:
        words = set(re.findall(r"[a-z]+", t))
        if "day after tomorrow" in t:
            req_date = today + timedelta(days=2)
        elif any(w in t for w in _TODAY_WORDS if " " in w) or words & {w for w in _TODAY_WORDS if " " not in w}:
            req_date = today
        elif words & set(_TOMORROW_WORDS):
            req_date = today + timedelta(days=1)
        else:
            for w in re.findall(r"[a-z]+", t):
                if w in _WEEKDAYS:
                    weekday = _WEEKDAYS[w]
                    break

    if hour is None:
        for m in _TIME_RE.finditer(t):
            h = int(m.group(1))
            mins = int(m.group(2)) if m.group(2) else 0
            ampm = (m.group(3) or "").replace(".", "").replace(" ", "")
            if mins > 59:
                continue
            if ampm:
                if not 1 <= h <= 12:
                    continue
                h = h % 12 + (12 if ampm.startswith("p") else 0)
            elif h > 23:
                continue
            elif 1 <= h <= 7:
                h += 12  # no am/pm: clinic is only open 10-1 and 5-7, so "6" means 6 PM
            hour, minute = h, mins
            break

    return req_date, weekday, hour, minute

def match_requested_slot(text: str, rows: List[Appointment], now: Optional[datetime] = None) -> Optional[Appointment]:
    """Pick the slot the patient asked for. A time is required; date/weekday narrow it down.
    Without a date the earliest matching time is chosen."""
    now = now or datetime.now(_get_tz())
    req_date, weekday, hour, minute = _parse_slot_request(text, now)
    if hour is None:
        return None
    for appt in sorted(rows, key=lambda a: _to_clinic_tz(a.slot)):
        d = _to_clinic_tz(appt.slot)
        if d.hour != hour or d.minute != minute:
            continue
        if req_date is not None and d.date() != req_date:
            continue
        if weekday is not None and d.weekday() != weekday:
            continue
        return appt
    return None

def phone_variants(phone: str) -> List[str]:
    """Stored phone formats for a number, with and without the 91 country code."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    variants = [digits]
    if digits.startswith("91") and len(digits) > 10:
        variants.append(digits[2:])
    elif len(digits) == 10:
        variants.append(f"91{digits}")
    return variants

# -----------------------------------------------------------------------------
# Agent tools
# -----------------------------------------------------------------------------

def book_slot(slot: str, name: str, phone: str) -> str:
    """Book an appointment for a patient and sync with Google Calendar."""
    clean_phone = "".join(c for c in (phone or "") if c.isdigit())
    clean_name = " ".join((name or "").split())[:100]
    if not clean_name:
        return "Cannot book yet: ask the patient for their full name first."

    seed_slots_if_needed()
    with db_session() as session:
        rows = available_slot_rows(session)
        matched = match_requested_slot(slot, rows)

        if not matched:
            preview = [_to_clinic_tz(a.slot).strftime("%a %d %b %I:%M %p") for a in rows[:5]]
            return (f"Slot '{slot}' is not available or did not name a specific date and time. "
                    f"Ask the patient to choose one of: {preview}")

        local_dt = _to_clinic_tz(matched.slot)
        pretty_slot = local_dt.strftime("%a %d %b %I:%M %p")
        if not claim_slot(session, matched.id, patient_name=clean_name, phone=clean_phone, status="booked"):
            return f"Sorry, {pretty_slot} was just taken by another patient. Call get_free_slots and offer another time."

        if calendar_service.is_available:
            g_event_id = calendar_service.create_booking_event(clean_name, clean_phone, local_dt.strftime("%Y-%m-%dT%H:%M:%S"))
            if g_event_id:
                session.query(Appointment).filter(Appointment.id == matched.id).update(
                    {"google_event_id": g_event_id}, synchronize_session=False)

        return f"Booked {clean_name} for {pretty_slot}"

def cancel_booking(phone: str) -> str:
    """Cancel the patient's next upcoming booking, delete it from Google Calendar, and mark it cancelled."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) < 10:
        return "Please provide a valid 10-digit phone number."

    now = datetime.now(_get_tz())

    with db_session() as session:
        appt = session.query(Appointment).filter(
            Appointment.phone.in_(phone_variants(digits)),
            Appointment.status.in_(["booked", "pending_payment"]),
            Appointment.slot > now
        ).order_by(Appointment.slot.asc()).first()

        if not appt:
            return "No active booking found for this phone number."

        # Remove from Google Calendar if event exists
        if appt.google_event_id and calendar_service.is_available:
            try:
                calendar_service.delete_booking_event(appt.google_event_id)
            except Exception as e:
                logger.error(f"Error removing Google Calendar event during cancel: {e}")

        local_dt = _to_clinic_tz(appt.slot)
        pretty_slot = local_dt.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")

        # Retain patient details and mark status as 'cancelled' for dashboard tracking.
        # The slot becomes bookable again; claim_slot clears these details when it is rebooked.
        appt.status = "cancelled"
        appt.notes = f"{appt.notes or ''} [Cancelled via AI on {now.strftime('%d %b %I:%M %p')}]".strip()
        appt.google_event_id = None
        appt.hold_expires_at = None

        return f"Your appointment for {pretty_slot} has been successfully cancelled."

# The patient's phone number is deliberately NOT a tool parameter: the agent always
# books and cancels for the WhatsApp number the message came from.
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_free_slots",
            "description": "List upcoming free clinic appointment slots",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_slot",
            "description": "Book a clinic appointment for the patient messaging you",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string", "description": "The exact slot string from get_free_slots"},
                    "name": {"type": "string", "description": "Full name of the patient"}
                },
                "required": ["slot", "name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": "Cancel the next upcoming appointment of the patient messaging you",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]
