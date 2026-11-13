"""Voice AI Receptionist API endpoints for Bolna.dev / Vobiz telephony integrations."""
import hmac
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from fastapi import APIRouter, Header, HTTPException, Query, BackgroundTasks

from app.core.config import settings
from app.core.database import db_session
from app.core.models import Appointment
from app.core.tools import (
    get_free_slots, cancel_booking, _to_clinic_tz, _get_tz, seed_slots_if_needed,
    available_slot_rows, match_requested_slot, claim_slot,
)
from app.core.calendar_service import calendar_service
from app.core.razorpay_service import razorpay_service
from app.core.jev_service import jev_service
from app.channels.whatsapp.client import send_message
from app.channels.voice.speech import _format_slots_for_speech, _clean_human_slot_labels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["Voice AI Receptionist"])

class VoiceBookingRequest(BaseModel):
    patient_name: str = Field(..., description="Full name of the caller/patient")
    phone: str = Field(..., description="Caller's phone number with or without country code")
    slot: str = Field(..., description="Desired slot time string (e.g., '11:00 AM', 'Tomorrow 11 AM')")
    notes: Optional[str] = Field(None, description="Patient symptoms or inquiry notes collected by Voice AI")

class VoiceCancelRequest(BaseModel):
    phone: str = Field(..., description="Patient phone number to cancel")

class VoiceTriageRequest(BaseModel):
    utterance: str = Field(..., description="Spoken utterance or inquiry from caller")
    phone: Optional[str] = Field(None, description="Caller's phone number if available")

def _verify_voice_secret(x_voice_secret: Optional[str]):
    """Authenticate Bolna/Vobiz calls with the shared X-Voice-Secret header.

    Without a configured secret these endpoints can book and cancel any patient's
    appointment, so they are only left open outside production.
    """
    if not settings.ENABLE_VOICE_AI:
        raise HTTPException(status_code=404, detail="Voice AI channel is disabled")
    if settings.VOICE_API_SECRET:
        if not x_voice_secret or not hmac.compare_digest(x_voice_secret, settings.VOICE_API_SECRET):
            raise HTTPException(status_code=401, detail="Unauthorized voice API request")
    elif settings.is_production:
        logger.error("VOICE_API_SECRET is not configured; rejecting voice API request in production.")
        raise HTTPException(status_code=401, detail="Voice API secret is not configured")

def _notify_whatsapp_booking_confirmed(phone: str, patient_name: str, slot_display: str):
    """Deliver immediate WhatsApp booking confirmation card."""
    text = (
        f"✅ *Dr. Rao's Clinic - Appointment Confirmed*\n\n"
        f"Hello *{patient_name}*, your appointment with Dr. Rao is confirmed!\n\n"
        f"📅 *Date & Time*: {slot_display}\n"
        f"📍 *Clinic Address*: {settings.CLINIC_ADDRESS}\n"
        f"🗺️ *Google Maps*: {settings.CLINIC_MAPS_URL}\n\n"
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

@router.get("/slots")
def get_voice_slots(
    date: Optional[str] = Query(None, description="Filter slots by date or keyword (e.g. 'today', 'tomorrow', 'monday')"),
    x_voice_secret: Optional[str] = Header(None)
):
    """
    Called by Bolna Voice AI Agent when caller asks for doctor availability.
    Returns structured slots, human-friendly labels, and natural spoken text.
    """
    _verify_voice_secret(x_voice_secret)
    raw_slots = get_free_slots()
    tz = _get_tz()
    now = datetime.now(tz)

    target_context = None

    # Clean and analyze date query
    if date:
        d_lower = date.lower().strip()
        generic_tokens = {"", "none", "null", "undefined", "{date}", "{slot_date}", "any", "all", "slots", "available", "can i know the available slots", "appointment", "timings"}
        if d_lower not in generic_tokens:
            today_date = now.date()
            tomorrow_date = (now + timedelta(days=1)).date()

            # 1. "today" / "aaj" / "eeroju"
            if any(k in d_lower for k in ("today", "aaj", "eeroju")):
                target_context = "today"
                today_iso = today_date.strftime("%Y-%m-%d")
                filtered = [s for s in raw_slots if today_iso in s]
                if filtered:
                    raw_slots = filtered
                else:
                    raw_slots = []

            # 2. "tomorrow" / "kal" / "repu"
            elif any(k in d_lower for k in ("tomorrow", "kal", "repu")):
                target_context = "tomorrow"
                tom_iso = tomorrow_date.strftime("%Y-%m-%d")
                filtered = [s for s in raw_slots if tom_iso in s]
                if filtered:
                    raw_slots = filtered
                else:
                    raw_slots = []

            # 3. Day of week filter (e.g. "monday", "tuesday")
            else:
                weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
                matched_day = next((w for w in weekdays if w in d_lower), None)
                if matched_day:
                    filtered = [s for s in raw_slots if matched_day[:3] in s.lower()]
                    if filtered:
                        raw_slots = filtered
                else:
                    filtered = [s for s in raw_slots if d_lower in s.lower()]
                    if filtered:
                        raw_slots = filtered

    spoken_text = _format_slots_for_speech(raw_slots, target_context=target_context)
    human_slots = _clean_human_slot_labels(raw_slots[:6])

    return {
        "status": "success",
        "count": len(raw_slots),
        "spoken_text": spoken_text,
        "slots": human_slots,
        "raw_slots": raw_slots[:6],
        "message": spoken_text
    }

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
    clean_phone = "".join(c for c in payload.phone if c.isdigit())
    name = " ".join(payload.patient_name.split())[:100] or "Patient"
    tz = _get_tz()
    now = datetime.now(tz)

    if len(clean_phone) < 10:
        return {
            "status": "invalid_phone",
            "spoken_text": "I didn't catch your complete phone number. Could you please say your full 10-digit mobile number?"
        }

    seed_slots_if_needed()

    with db_session() as session:
        rows = available_slot_rows(session, now)
        matched_slot = match_requested_slot(payload.slot, rows, now)

        if not matched_slot:
            return {
                "status": "slot_unavailable",
                "spoken_text": (
                    f"I'm sorry, I couldn't find an open slot for {payload.slot}. "
                    "Could you tell me the day and time you'd like, or shall I read out the available times?"
                )
            }

        appt_id = matched_slot.id
        local_dt = _to_clinic_tz(matched_slot.slot)
        pretty_slot = local_dt.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
        slot_iso = local_dt.strftime("%Y-%m-%dT%H:%M:%S")
        taken_reply = {
            "status": "slot_unavailable",
            "spoken_text": f"I'm sorry, {pretty_slot} was just booked by another caller. Would you like me to check other available times?"
        }

        # -------------------------------------------------------------
        # Mode A: Razorpay Advance Token Payment Enabled
        # -------------------------------------------------------------
        if settings.ENABLE_RAZORPAY:
            hold_minutes = settings.SLOT_HOLD_MINUTES
            amount = settings.ADVANCE_TOKEN_AMOUNT_INR

            # Hold the slot provisionally
            if not claim_slot(
                session, appt_id,
                patient_name=name,
                phone=clean_phone,
                status="pending_payment",
                payment_status="pending",
                payment_amount=amount,
                hold_expires_at=now + timedelta(minutes=hold_minutes),
                notes=payload.notes,
            ):
                return taken_reply

            # Generate Razorpay UPI Link
            link_info = razorpay_service.create_payment_link(
                appointment_id=appt_id,
                patient_name=name,
                phone=clean_phone,
                amount_inr=amount,
                slot_display=pretty_slot,
                hold_minutes=hold_minutes
            )
            pay_url = link_info.get("short_url")
            session.query(Appointment).filter(Appointment.id == appt_id).update(
                {"payment_link_id": link_info.get("id"), "payment_link_url": pay_url},
                synchronize_session=False
            )

            # Dispatch WhatsApp payment link in background
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
                "appointment_id": appt_id,
                "slot": pretty_slot,
                "payment_url": pay_url,
                "amount": amount,
                "spoken_text": spoken_text
            }

        # -------------------------------------------------------------
        # Mode B: Instant Confirmation (Free / Cash on Arrival)
        # -------------------------------------------------------------
        if not claim_slot(
            session, appt_id,
            patient_name=name,
            phone=clean_phone,
            status="booked",
            notes=payload.notes,
        ):
            return taken_reply

        g_event_id = None
        if calendar_service.is_available:
            g_event_id = calendar_service.create_booking_event(name, clean_phone, slot_iso)
            if g_event_id:
                session.query(Appointment).filter(Appointment.id == appt_id).update(
                    {"google_event_id": g_event_id}, synchronize_session=False)

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
            "appointment_id": appt_id,
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
    raw_phone = payload.phone.lstrip("+").strip()
    digits = "".join(c for c in raw_phone if c.isdigit())

    # Prevent accidental cancellations if caller only spoke a country code or partial number
    if len(digits) < 10:
        return {
            "status": "invalid_phone",
            "spoken_text": "I didn't catch your complete phone number. Could you please say your full 10-digit mobile number so I can locate your booking?"
        }

    clean_phone = digits
    result = cancel_booking(clean_phone)

    if "successfully cancelled" in result.lower():
        send_message(
            clean_phone,
            f"❌ *Dr. Rao's Clinic - Booking Cancelled*\n\n{result}\n\nIf you would like to reschedule at any time, simply reply to this message."
        )
        spoken_text = f"{result} I have also sent a confirmation to your WhatsApp. Let us know if you'd like to schedule for another time."
        return {"status": "cancelled", "message": result, "spoken_text": spoken_text}
    else:
        spoken_text = "I couldn't find an active appointment under that phone number. Could you please confirm the 10-digit number you booked with?"
        return {"status": "not_found", "message": result, "spoken_text": spoken_text}

@router.post("/triage")
def voice_triage(
    payload: VoiceTriageRequest,
    x_voice_secret: Optional[str] = Header(None)
):
    """Sub-second TypeSafe Jev System-One triage for inbound voice calls (Bolna/Vobiz)."""
    _verify_voice_secret(x_voice_secret)
    triage = jev_service.triage_patient_message(payload.utterance)
    suggested_action = "human_emergency_transfer" if triage.is_emergency else triage.intent
    
    emergency_speech = (
        "This sounds like an urgent medical situation. Please hang up and immediately dial 1 0 8 for an emergency ambulance, "
        "or proceed directly to the nearest hospital emergency room. I will also notify our front desk."
    )
    
    return {
        "is_emergency": triage.is_emergency,
        "intent": triage.intent,
        "urgency": triage.urgency,
        "confidence": triage.confidence,
        "source": triage.source,
        "should_escalate_to_human": triage.is_emergency or triage.urgency == "urgent",
        "suggested_action": suggested_action,
        "emergency_speech": emergency_speech if triage.is_emergency else None
    }
