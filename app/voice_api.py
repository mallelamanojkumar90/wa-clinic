"""Voice AI Receptionist API endpoints for Bolna.dev / Vobiz telephony integrations."""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import pytz
from pydantic import BaseModel, Field
from fastapi import APIRouter, Header, HTTPException, Query, BackgroundTasks

from app.config import settings
from app.database import db_session
from app.models import Appointment
from app.tools import get_free_slots, book_slot, cancel_booking, _to_clinic_tz, _get_tz, seed_slots_if_needed
from app.calendar_service import calendar_service
from app.whatsapp import send_message
from app.razorpay_service import razorpay_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["Voice AI Receptionist"])

class VoiceBookingRequest(BaseModel):
    patient_name: str = Field(..., description="Full name of the caller/patient")
    phone: str = Field(..., description="Caller's phone number with or without country code")
    slot: str = Field(..., description="Desired slot time string (e.g., '11:00 AM', 'Tomorrow 11 AM')")
    notes: Optional[str] = Field(None, description="Patient symptoms or inquiry notes collected by Voice AI")

class VoiceCancelRequest(BaseModel):
    phone: str = Field(..., description="Patient phone number to cancel")

def _verify_voice_secret(x_voice_secret: Optional[str]):
    """Optional security verification for Bolna webhook calls."""
    if settings.VOICE_API_SECRET and x_voice_secret != settings.VOICE_API_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized voice API request")

def _format_slots_for_speech(slots: List[str]) -> str:
    """Transform ISO slots into natural spoken English for Voice AI synthesis."""
    if not slots:
        return "Dr. Rao currently has no open slots available for the next few days. Would you like me to take a message for the front desk?"

    spoken_slots = []
    for s in slots[:4]:
        # Typical format: 2026-09-19T10:00:00 (Sat 19 Sep 10:00 AM)
        if "(" in s and ")" in s:
            display = s.split("(")[-1].rstrip(")")
            spoken_slots.append(display)
        else:
            spoken_slots.append(s)

    if len(spoken_slots) == 1:
        return f"Dr. Rao has an opening on {spoken_slots[0]}. Would you like me to book that for you?"
    elif len(spoken_slots) == 2:
        return f"Dr. Rao has openings on {spoken_slots[0]} and {spoken_slots[1]}. Which one works best for you?"
    else:
        slots_str = ", ".join(spoken_slots[:-1]) + f", and {spoken_slots[-1]}"
        return f"Dr. Rao is available on {slots_str}. Which time would you prefer?"

@router.get("/slots")
def get_voice_slots(
    date: Optional[str] = Query(None, description="Filter slots by date or keyword (e.g. 'tomorrow')"),
    x_voice_secret: Optional[str] = Header(None)
):
    """
    Called by Bolna Voice AI Agent when caller asks for doctor availability.
    Returns both structured slots and a ready-to-synthesize speech text.
    """
    _verify_voice_secret(x_voice_secret)
    raw_slots = get_free_slots()

    # Optional filter by date or day
    if date:
        d_lower = date.lower().strip()
        filtered = [s for s in raw_slots if d_lower in s.lower()]
        if filtered:
            raw_slots = filtered

    spoken_text = _format_slots_for_speech(raw_slots)
    return {
        "status": "success",
        "count": len(raw_slots),
        "slots": raw_slots[:6],
        "spoken_text": spoken_text
    }

def _notify_whatsapp_booking_confirmed(phone: str, patient_name: str, slot_display: str):
    """Deliver immediate WhatsApp booking confirmation card."""
    text = (
        f"✅ *Dr. Rao's Clinic - Appointment Confirmed*\n\n"
        f"Hello *{patient_name}*, your appointment with Dr. Rao is confirmed!\n\n"
        f"📅 *Date & Time*: {slot_display}\n"
        f"📍 *Clinic Address*: Dr. Rao's Clinic, Road No. 36, Jubilee Hills, Hyderabad\n"
        f"🗺️ *Google Maps*: https://maps.google.com/?q=Dr+Raos+Clinic+Hyderabad\n\n"
        f"If you need to reschedule or cancel at any time, just reply to this message!"
    )
    send_message(phone, text)

def _notify_whatsapp_provisional_hold(phone: str, patient_name: str, slot_display: str, amount_inr: int, pay_url: str, hold_minutes: int):
    """Deliver WhatsApp advance token payment request with 1-tap UPI link."""
    text = (
        f"🏥 *Dr. Rao's Clinic - Advance Token Reservation*\n\n"
        f"Hello *{patient_name}*, we have reserved your requested appointment slot:\n"
        f"📅 *{slot_display}*\n\n"
        f"To confirm your booking, please complete the advance token payment of *₹{amount_inr}* within *{hold_minutes} minutes*:\n\n"
        f"👉 *Tap to Pay via UPI / GPay / PhonePe*:\n"
        f"{pay_url}\n\n"
        f"ℹ️ *Note*: Unpaid slots are automatically released after {hold_minutes} minutes. Once paid, you'll receive your confirmed appointment token."
    )
    send_message(phone, text)

@router.post("/book")
def book_voice_slot(
    payload: VoiceBookingRequest,
    background_tasks: BackgroundTasks,
    x_voice_secret: Optional[str] = Header(None)
):
    """
    Called by Bolna Voice AI Agent when caller confirms their appointment slot.
    Reserves slot, generates Razorpay UPI payment link (if enabled), and sends WhatsApp confirmation.
    """
    _verify_voice_secret(x_voice_secret)
    clean_phone = payload.phone.lstrip("+").strip()
    name = payload.patient_name.strip() or "Patient"
    want = payload.slot.replace(",", "").replace("  ", " ").lower().strip()
    tz = _get_tz()
    now = datetime.now(tz)

    seed_slots_if_needed()

    with db_session() as session:
        # Query free upcoming slots
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
            # Fallback: if caller gave partial time like "11" or "morning", pick the first matching slot
            for appt in rows:
                local_d = _to_clinic_tz(appt.slot)
                time_str = local_d.strftime("%I:%M %p").lower()
                if want in time_str or time_str in want:
                    matched_slot = appt
                    break

        if not matched_slot:
            return {
                "status": "slot_unavailable",
                "spoken_text": f"I'm sorry, the {payload.slot} slot is no longer available. Would you like me to check other available times for you?"
            }

        local_dt = _to_clinic_tz(matched_slot.slot)
        pretty_slot = local_dt.strftime("%a %d %b at %I:%M %p")
        slot_iso = local_dt.strftime("%Y-%m-%dT%H:%M:%S")

        # -------------------------------------------------------------
        # Mode A: Razorpay Advance Token Payment Enabled
        # -------------------------------------------------------------
        if settings.ENABLE_RAZORPAY:
            hold_minutes = settings.SLOT_HOLD_MINUTES
            amount = settings.ADVANCE_TOKEN_AMOUNT_INR
            hold_expires = now + timedelta(minutes=hold_minutes)

            # Hold the slot provisionally
            matched_slot.patient_name = name
            matched_slot.phone = clean_phone
            matched_slot.status = "pending_payment"
            matched_slot.payment_status = "pending"
            matched_slot.payment_amount = amount
            matched_slot.hold_expires_at = hold_expires
            matched_slot.notes = payload.notes
            session.flush()

            # Generate Razorpay UPI Link
            link_info = razorpay_service.create_payment_link(
                appointment_id=matched_slot.id,
                patient_name=name,
                phone=clean_phone,
                amount_inr=amount,
                slot_display=pretty_slot,
                hold_minutes=hold_minutes
            )

            matched_slot.payment_link_id = link_info.get("id")
            matched_slot.payment_link_url = link_info.get("short_url")

            # Dispatch WhatsApp payment link in background
            pay_url = link_info.get("short_url")
            background_tasks.add_task(
                _notify_whatsapp_provisional_hold,
                clean_phone,
                name,
                pretty_slot,
                amount,
                pay_url,
                hold_minutes
            )

            spoken_text = (
                f"Thank you, {name}! I have reserved your appointment with Dr. Rao for {pretty_slot}. "
                f"I just sent the UPI advance payment link of {amount} rupees directly to your WhatsApp. "
                f"Please complete the payment within {hold_minutes} minutes to confirm your booking. "
                f"Have a great day!"
            )

            return {
                "status": "pending_payment",
                "appointment_id": matched_slot.id,
                "slot": pretty_slot,
                "payment_url": pay_url,
                "amount": amount,
                "spoken_text": spoken_text
            }

        # -------------------------------------------------------------
        # Mode B: Instant Confirmation (Free / Cash on Arrival)
        # -------------------------------------------------------------
        else:
            g_event_id = None
            if calendar_service.is_available:
                g_event_id = calendar_service.create_booking_event(name, clean_phone, slot_iso)

            matched_slot.patient_name = name
            matched_slot.phone = clean_phone
            matched_slot.status = "booked"
            matched_slot.payment_status = "none"
            matched_slot.google_event_id = g_event_id
            matched_slot.notes = payload.notes

            # Dispatch WhatsApp confirmation in background
            background_tasks.add_task(
                _notify_whatsapp_booking_confirmed,
                clean_phone,
                name,
                pretty_slot
            )

            spoken_text = (
                f"Your appointment with Dr. Rao is confirmed for {pretty_slot}! "
                f"I have sent the booking details and clinic location directly to your WhatsApp. "
                f"Thank you for calling Dr. Rao's Clinic!"
            )

            return {
                "status": "confirmed",
                "appointment_id": matched_slot.id,
                "slot": pretty_slot,
                "google_event_id": g_event_id,
                "spoken_text": spoken_text
            }

@router.post("/cancel")
def cancel_voice_booking(
    payload: VoiceCancelRequest,
    x_voice_secret: Optional[str] = Header(None)
):
    """Called by Bolna Voice AI Agent when caller asks to cancel their booking."""
    _verify_voice_secret(x_voice_secret)
    clean_phone = payload.phone.lstrip("+").strip()
    result = cancel_booking(clean_phone)

    if "successfully cancelled" in result.lower():
        send_message(
            clean_phone,
            "❌ *Dr. Rao's Clinic*: Your appointment has been cancelled as requested over phone call. Reply to this message if you would like to book a new slot."
        )
        spoken_text = "Your appointment has been cancelled. We've sent a confirmation to your WhatsApp. Let us know if you'd like to schedule for another time."
        return {"status": "cancelled", "spoken_text": spoken_text}
    else:
        spoken_text = "I couldn't find an active booking under this phone number. Would you like me to check available slots to book a new appointment?"
        return {"status": "not_found", "spoken_text": spoken_text}
