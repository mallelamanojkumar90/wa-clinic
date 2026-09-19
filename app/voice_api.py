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

def _format_slots_for_speech(slots: List[str], target_context: Optional[str] = None) -> str:
    """Transform ISO slots into natural, warm spoken English for Voice AI synthesis."""
    if not slots:
        if target_context == "today":
            return "Dr. Rao has no remaining slots available today. Would you like me to check upcoming openings for Monday?"
        elif target_context == "tomorrow":
            return "Dr. Rao's clinic is closed tomorrow on Sunday. The next available openings are on Monday. Would you like me to reserve a Monday slot?"
        return "Dr. Rao currently has no open slots available for the next few days. Would you like me to take a message for the front desk?"

    tz = _get_tz()
    now = datetime.now(tz)
    today_date = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()

    # Group slots by calendar date
    by_day: Dict[Any, List[str]] = {}
    for s in slots:
        # Expected: 2026-09-19T17:00:00 (Sat 19 Sep 05:00 PM)
        try:
            iso_part = s.split(" ")[0].strip()
            dt = datetime.fromisoformat(iso_part)
            if dt.tzinfo is None:
                dt = tz.localize(dt)
            else:
                dt = dt.astimezone(tz)
            d = dt.date()
            time_str = dt.strftime("%I:%M %p").lstrip("0")
            if d not in by_day:
                by_day[d] = []
            if time_str not in by_day[d]:
                by_day[d].append(time_str)
        except Exception:
            continue

    if not by_day:
        return "Dr. Rao has appointments available. Which time would you prefer?"

    phrases = []
    days_to_show = list(by_day.keys())[:2]
    for d in days_to_show:
        times = by_day[d]
        if d == today_date:
            day_label = "today"
        elif d == tomorrow_date:
            day_label = "tomorrow"
        else:
            day_label = d.strftime("on %A, %B %d").replace(" 0", " ")

        if len(times) == 1:
            times_formatted = times[0]
        elif len(times) == 2:
            times_formatted = f"{times[0]} and {times[1]}"
        else:
            times_formatted = f"{', '.join(times[:2])}, and {times[2]}"

        phrases.append(f"{day_label} at {times_formatted}")

    if len(phrases) == 1:
        return f"Dr. Rao is available {phrases[0]}. Which time works best for you?"
    else:
        return f"Dr. Rao is available {phrases[0]}, or {phrases[1]}. Which time would you prefer?"

def _clean_human_slot_labels(slots: List[str]) -> List[str]:
    """Convert raw slot strings into clean human phrases like 'Today at 6:00 PM'."""
    tz = _get_tz()
    now = datetime.now(tz)
    today_date = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()

    labels = []
    for s in slots:
        try:
            iso_part = s.split(" ")[0].strip()
            dt = datetime.fromisoformat(iso_part)
            if dt.tzinfo is None:
                dt = tz.localize(dt)
            else:
                dt = dt.astimezone(tz)
            d = dt.date()
            time_str = dt.strftime("%I:%M %p").lstrip("0")
            if d == today_date:
                labels.append(f"Today at {time_str}")
            elif d == tomorrow_date:
                labels.append(f"Tomorrow at {time_str}")
            else:
                labels.append(dt.strftime(f"%A %d %b at {time_str}").replace(" 0", " "))
        except Exception:
            labels.append(s)
    return labels

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
        # Ignore template placeholders from Bolna like {date}, {slot_date}, null, empty or generic questions
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
                    # No more slots today - leave raw_slots so fallback next available days can be offered
                    raw_slots = []

            # 2. "tomorrow" / "kal" / "repu"
            elif any(k in d_lower for k in ("tomorrow", "kal", "repu")):
                target_context = "tomorrow"
                tom_iso = tomorrow_date.strftime("%Y-%m-%d")
                filtered = [s for s in raw_slots if tom_iso in s]
                if filtered:
                    raw_slots = filtered
                else:
                    # Tomorrow might be Sunday or fully booked
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
                    # Generic keyword search against date display
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
            iso_str = local_d.strftime("%Y-%m-%dT%H:%M:%S").lower()
            pretty = local_d.strftime("%a %d %b %I:%M %p").lower()
            pretty_no_zero = local_d.strftime("%a %d %b %I:%M %p").replace(" 0", " ").lower()
            time_no_zero = local_d.strftime("%I:%M %p").lstrip("0").lower()
            time_short = time_no_zero.replace(":00", "").strip()
            weekday = local_d.strftime("%A").lower()
            month_name = local_d.strftime("%B").lower()
            day_num = local_d.strftime("%d").lstrip("0")
            human_label = f"{weekday} {day_num} {month_name} at {time_no_zero}".lower()
            today_label = f"today at {time_short}" if local_d.date() == now.date() else ""
            tomorrow_label = f"tomorrow at {time_short}" if local_d.date() == (now + timedelta(days=1)).date() else ""

            candidates = [iso_str, pretty, pretty_no_zero, time_no_zero, time_short, weekday, human_label]
            if today_label:
                candidates.extend([today_label, "today"])
            if tomorrow_label:
                candidates.extend([tomorrow_label, "tomorrow"])

            # Direct or substring match
            if any(want in c or c in want for c in candidates if c):
                matched_slot = appt
                break

        if not matched_slot:
            # Fallback: match partial hour numbers e.g. "6", "11", "5", "morning", "evening"
            for appt in rows:
                local_d = _to_clinic_tz(appt.slot)
                hour_12 = local_d.strftime("%I").lstrip("0")
                if hour_12 and (f" {hour_12} " in f" {want} " or f"{hour_12}pm" in want.replace(" ", "") or f"{hour_12}am" in want.replace(" ", "")):
                    matched_slot = appt
                    break

        # Fallback: if slot is available on Google Calendar but not in DB appointments
        if not matched_slot and calendar_service.is_available:
            try:
                cal_slots = calendar_service.get_free_slots(days_ahead=3)
                for iso, display in cal_slots:
                    dt = _to_clinic_tz(datetime.fromisoformat(iso))
                    w_day = dt.strftime("%A").lower()
                    w_time = dt.strftime("%I:%M %p").lstrip("0").lower()
                    cands = [iso.lower(), display.lower(), w_day, w_time, w_time.replace(":00", "")]
                    if any(want in c or c in want for c in cands):
                        matched_slot = Appointment(patient_name="", phone="", slot=dt, status="free")
                        session.add(matched_slot)
                        session.flush()
                        break
            except Exception as e:
                logger.warning(f"Google Calendar fallback match error: {e}")

        if not matched_slot:
            return {
                "status": "slot_unavailable",
                "spoken_text": f"I'm sorry, the {payload.slot} slot is no longer available. Would you like me to check other available times for you?"
            }

        local_dt = _to_clinic_tz(matched_slot.slot)
        pretty_slot = local_dt.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
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
