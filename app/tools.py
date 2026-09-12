"""Receptionist tools callable by the LLM agent: get_free_slots, book_slot, cancel_booking."""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
import pytz
from sqlalchemy import select, update
from app.config import settings
from app.database import db_session
from app.models import Appointment
from app.calendar_service import calendar_service

logger = logging.getLogger(__name__)

def _get_tz():
    return pytz.timezone(settings.CLINIC_TIMEZONE)

def _to_clinic_tz(d):
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if d.tzinfo is None:
        d = pytz.utc.localize(d)
    return d.astimezone(_get_tz())

def seed_slots_if_needed():
    """Ensure upcoming slots exist in the database."""
    tz = _get_tz()
    now = datetime.now(tz)
    with db_session() as session:
        # Clean up unbooked slots in the past
        session.query(Appointment).filter(
            Appointment.status == "free",
            Appointment.slot < now
        ).delete()

        # Seed next 3 business days (Mon-Sat)
        for day in range(1, 4):
            target_date = (now + timedelta(days=day)).date()
            if target_date.weekday() == 6:  # Skip Sunday
                continue
            for hour in (10, 11, 12, 17, 18):
                from datetime import time as dt_time
                slot_dt = tz.localize(datetime.combine(target_date, dt_time(hour, 0, 0)))
                existing = session.query(Appointment).filter_by(slot=slot_dt).first()
                if not existing:
                    session.add(Appointment(patient_name="", phone="", slot=slot_dt, status="free"))

def get_free_slots() -> List[str]:
    """Retrieve available appointment slots (from Google Calendar if active, or DB)."""
    # 1. If Google Calendar is active, query live availability
    if calendar_service.is_available:
        cal_slots = calendar_service.get_free_slots(days_ahead=3)
        if cal_slots:
            return [display for _, display in cal_slots]

    # 2. Database slot fallback
    seed_slots_if_needed()
    now = datetime.now(_get_tz())
    with db_session() as session:
        rows = session.query(Appointment).filter(
            Appointment.status == "free",
            Appointment.slot > now
        ).order_by(Appointment.slot).all()

        out = []
        for appt in rows:
            local_d = _to_clinic_tz(appt.slot)
            iso_str = local_d.strftime("%Y-%m-%dT%H:%M:%S")
            pretty_str = local_d.strftime("%a %d %b %I:%M %p")
            out.append(f"{iso_str} ({pretty_str})")
        return out

def book_slot(slot: str, name: str, phone: str) -> str:
    """Book an appointment for a patient and sync with Google Calendar."""
    clean_phone = phone.lstrip("+")
    now = datetime.now(_get_tz())
    want = slot.replace(",", "").replace("  ", " ").lower().strip()

    with db_session() as session:
        # Retrieve all free upcoming slots
        rows = session.query(Appointment).filter(
            Appointment.status == "free",
            Appointment.slot > now
        ).all()

        matched_slot = None
        for appt in rows:
            local_d = _to_clinic_tz(appt.slot)
            iso_str = local_d.strftime("%Y-%m-%dT%H:%M:%S")
            pretty = local_d.strftime("%a %d %b %I:%M %p").replace(", ", " ").lower()

            if want in iso_str.lower() or want in pretty or pretty in want:
                matched_slot = appt
                break

        if not matched_slot:
            available_preview = []
            for a in rows[:5]:
                local_d = _to_clinic_tz(a.slot)
                available_preview.append(local_d.strftime("%a %d %b %I:%M %p"))
            return f"Slot '{slot}' not found or already booked. Available: {available_preview}"

        # Synchronize with Google Calendar if enabled
        g_event_id = None
        local_dt = _to_clinic_tz(matched_slot.slot)
        matched_slot_iso = local_dt.strftime("%Y-%m-%dT%H:%M:%S")
        pretty_slot = local_dt.strftime("%a %d %b %I:%M %p")

        if calendar_service.is_available:
            g_event_id = calendar_service.create_booking_event(name, clean_phone, matched_slot_iso)

        # Update database record atomically
        matched_slot.patient_name = name
        matched_slot.phone = clean_phone
        matched_slot.status = "booked"
        matched_slot.google_event_id = g_event_id

        return f"Booked {name} for {pretty_slot}"

def cancel_booking(phone: str) -> str:
    """Cancel patient's existing booking and delete from Google Calendar."""
    clean_phone = phone.lstrip("+")
    with db_session() as session:
        appt = session.query(Appointment).filter(
            Appointment.phone == clean_phone,
            Appointment.status == "booked"
        ).order_by(Appointment.slot.desc()).first()

        if not appt:
            return "No active booking found for this phone number."

        # Remove from Google Calendar if event exists
        if appt.google_event_id and calendar_service.is_available:
            calendar_service.delete_booking_event(appt.google_event_id)

        # Reset appointment status to free
        appt.patient_name = ""
        appt.phone = ""
        appt.status = "free"
        appt.google_event_id = None

        return "Your appointment has been successfully cancelled."

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
            "description": "Book a clinic appointment for a patient",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string", "description": "The exact slot string from get_free_slots"},
                    "name": {"type": "string", "description": "Full name of the patient"},
                    "phone": {"type": "string", "description": "Patient's phone number with country code"}
                },
                "required": ["slot", "name", "phone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": "Cancel a patient's booked appointment by phone number",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Patient phone number"}
                },
                "required": ["phone"]
            }
        }
    }
]
