"""FastAPI production entrypoint for Dr. Rao Clinic WhatsApp & Voice AI Receptionist."""
import json
import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, BackgroundTasks

from app.core.config import settings
from app.core.database import init_db, db_session
from app.core.models import Appointment
from app.core.calendar_service import calendar_service
from app.core.tools import _to_clinic_tz
from app.core.jev_service import jev_service
from app.core.razorpay_service import razorpay_service
from app.core.scheduler import start_scheduler, stop_scheduler

from app.channels.whatsapp import whatsapp_router, send_message, mark_as_read
from app.channels.whatsapp.router import (
    process_patient_message,
    is_duplicate_webhook,
    meta_webhook_verification,
    meta_webhook_handler,
)
from app.channels.whatsapp.agent import reply, save_chat_message
from app.channels.voice import voice_router
from app.dashboard import dashboard_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("clinic_receptionist")

def _log_production_config_problems():
    """Loudly report settings that leave patient data or bookings exposed in production."""
    if not settings.is_production:
        logger.warning("APP_ENV is not 'production': unsigned webhooks and unauthenticated voice calls are accepted.")
        return
    problems = []
    if not settings.META_APP_SECRET:
        problems.append("META_APP_SECRET is missing: WhatsApp webhooks will be rejected")
    if not settings.VERIFY_TOKEN:
        problems.append("VERIFY_TOKEN is missing: Meta webhook verification will fail")
    if settings.ENABLE_VOICE_AI and not settings.VOICE_API_SECRET:
        problems.append("VOICE_API_SECRET is missing: voice API calls will be rejected")
    if settings.ENABLE_RAZORPAY and not settings.RAZORPAY_WEBHOOK_SECRET:
        problems.append("RAZORPAY_WEBHOOK_SECRET is missing: payment confirmations will be rejected")
    if settings.DASHBOARD_PIN.strip() in ("", "1234") or len(settings.DASHBOARD_PIN.strip()) < 6:
        problems.append("DASHBOARD_PIN is the default or shorter than 6 characters: dashboard login is disabled")
    for p in problems:
        logger.error(f"[config] {p}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_production_config_problems()
    # Startup: Initialize database schema with retry resilience
    logger.info("Initializing database tables...")
    try:
        db_ready = init_db()
        if db_ready:
            logger.info(f"Database ready: {'PostgreSQL (Supabase)' if settings.is_postgres else 'SQLite (Local)'}")
        else:
            logger.warning(
                "Database could not be initialized during startup. "
                "The database may still be waking up. The server will start in degraded mode and retry on demand."
            )
    except Exception as exc:
        logger.error(
            f"Unexpected error during database initialization: {exc}. "
            "Continuing startup to allow health checks and incoming requests to function."
        )

    # Startup: Launch automated appointment reminder background scheduler
    try:
        start_scheduler()
    except Exception as exc:
        logger.error(f"Failed to start appointment reminder scheduler: {exc}")

    yield

    # Shutdown: Stop scheduler gracefully
    try:
        stop_scheduler()
    except Exception as exc:
        logger.error(f"Error stopping scheduler: {exc}")

app = FastAPI(
    title="Dr. Rao's Clinic - Multi-Channel AI Receptionist API",
    description=(
        "Dual-Channel Receptionist Architecture:\n"
        "1. WhatsApp AI Channel: Meta Cloud API Webhook, Multi-lingual GPT-4o-mini Agent, Voice Notes\n"
        "2. Voice AI Telephony Channel: Bolna.dev / Vobiz Voice REST API endpoints for speech synthesis"
    ),
    version="3.0.0",
    lifespan=lifespan
)

# -----------------------------------------------------------------------------
# Mount Channels & Portals
# -----------------------------------------------------------------------------

# Path 1: WhatsApp AI Receptionist (/webhook and /api/whatsapp/...)
app.include_router(whatsapp_router)

# Path 2: Telephony Voice AI Receptionist (/api/voice/...)
app.include_router(voice_router)

# Clinic Staff Administration Web Portal (/dashboard/...)
app.include_router(dashboard_router)

# -----------------------------------------------------------------------------
# Root Gateway & Health Check
# -----------------------------------------------------------------------------

@app.get("/")
def health_check():
    """Unified API Gateway Status: lists both WhatsApp & Voice AI routes and system integrations."""
    return {
        "status": "online",
        "service": "Dr. Rao Clinic Multi-Channel AI Receptionist",
        "version": "3.0.0",
        "channels": {
            "whatsapp": {
                "name": "WhatsApp AI Receptionist",
                "webhook_path": "/webhook",
                "webhook_alias": "/api/whatsapp/webhook",
                "audio_transcription": bool(settings.GROQ_API_KEY or settings.OPENAI_API_KEY),
                "status": "active" if bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_ID) else "simulation_mode"
            },
            "voice_ai": {
                "name": "Telephony Voice AI Receptionist (Bolna/Vobiz)",
                "enabled": settings.ENABLE_VOICE_AI,
                "endpoints": [
                    "/api/voice/slots",
                    "/api/voice/book",
                    "/api/voice/cancel",
                    "/api/voice/triage"
                ]
            }
        },
        "voice_ai_enabled": settings.ENABLE_VOICE_AI,
        "voice_endpoints": [
            "/api/voice/slots",
            "/api/voice/book",
            "/api/voice/cancel",
            "/api/voice/triage"
        ],
        "razorpay_enabled": settings.ENABLE_RAZORPAY,
        "database": "postgresql (supabase)" if settings.is_postgres else "sqlite (local)",
        "google_calendar_sync": calendar_service.is_available,
        "reminders_enabled": settings.ENABLE_REMINDERS,
        "audio_transcription": bool(settings.GROQ_API_KEY or settings.OPENAI_API_KEY),
        "advance_token_inr": settings.ADVANCE_TOKEN_AMOUNT_INR,
        "jev_system_one_triage": jev_service.is_available,
        "dashboard_url": "/dashboard",
        "model": settings.MODEL
    }

# -----------------------------------------------------------------------------
# Payment Webhook: Razorpay UPI Advance Token Callbacks
# -----------------------------------------------------------------------------

@app.post("/webhook/razorpay")
async def razorpay_webhook_handler(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Incoming Razorpay Payment Gateway Webhook for advance token payments."""
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not razorpay_service.verify_webhook_signature(raw_body, signature):
        logger.warning("Invalid Razorpay webhook signature.")
        return Response(content="Invalid signature", status_code=400)

    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        logger.error(f"Invalid JSON from Razorpay: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    event = data.get("event")
    logger.info(f"Received Razorpay webhook event: {event}")

    # Only payment links are issued by this app, so payment_link.paid is the one event that
    # confirms a booking. (payment.captured fires for the same payment and is ignored.)
    if event != "payment_link.paid":
        return {"status": "ignored"}

    payload_data = data.get("payload", {})
    plink_entity = payload_data.get("payment_link", {}).get("entity", {})
    payment_entity = payload_data.get("payment", {}).get("entity", {})

    notes = plink_entity.get("notes") or {}
    appt_id = notes.get("appointment_id")
    plink_id = plink_entity.get("id")
    payer_phone = "".join(c for c in str(notes.get("phone", "")) if c.isdigit())
    payment_id = payment_entity.get("id") or "UPI_DIRECT"
    amount_paid = (plink_entity.get("amount_paid") or payment_entity.get("amount") or 0) // 100

    if not plink_id:
        logger.warning("Razorpay payment_link.paid webhook without a payment link id; ignoring.")
        return {"status": "ignored"}

    with db_session() as session:
        appt = session.query(Appointment).filter_by(payment_link_id=plink_id).first()
        if not appt and appt_id:
            try:
                appt = session.query(Appointment).filter_by(id=int(appt_id)).first()
            except (ValueError, TypeError):
                appt = None

        if not appt:
            logger.warning(f"No appointment found matching Razorpay webhook (appt_id={appt_id}, plink_id={plink_id})")
            return {"status": "ignored"}

        # Idempotency: Razorpay retries webhooks; don't duplicate calendar events or messages
        if appt.payment_link_id == plink_id and appt.status == "booked" and appt.payment_status == "paid":
            logger.info(f"Appointment {appt.id} already paid and booked. Skipping duplicate webhook.")
            return {"status": "already_processed"}

        local_dt = _to_clinic_tz(appt.slot)
        pretty_slot = local_dt.strftime("%a %d %b at %I:%M %p")
        slot_iso = local_dt.strftime("%Y-%m-%dT%H:%M:%S")

        # Promote only if this payment link still holds the slot. The conditional update
        # also loses cleanly to the hold-expiry job if both run at the same moment.
        promoted = session.query(Appointment).filter(
            Appointment.id == appt.id,
            Appointment.status == "pending_payment",
            Appointment.payment_link_id == plink_id,
        ).update(
            {"status": "booked", "payment_status": "paid", "hold_expires_at": None},
            synchronize_session=False
        )

        if promoted != 1:
            # The hold expired (or was cancelled) and the slot may now belong to someone else.
            # Never touch that slot: the payer needs a refund instead.
            logger.error(
                f"[REFUND REQUIRED] Late Razorpay payment {payment_id} of Rs {amount_paid} for link {plink_id}: "
                f"appointment {appt.id} ({pretty_slot}) is no longer held by this payment (status={appt.status})."
            )
            if payer_phone:
                late_text = (
                    f"⚠️ *Dr. Rao's Clinic - Payment Received After Reservation Expired*\n\n"
                    f"We received your payment of *₹{amount_paid}* (Ref: {payment_id}), but your reservation for "
                    f"*{pretty_slot}* had already expired.\n\n"
                    f"Your payment will be refunded. Reply to this message if you'd like to book a new slot."
                )
                background_tasks.add_task(send_message, payer_phone, late_text)
            return {"status": "late_payment_refund_required", "appointment_id": appt.id}

        session.expire_all()
        appt = session.query(Appointment).filter_by(id=appt.id).first()

        # Sync with Google Calendar
        if calendar_service.is_available and not appt.google_event_id:
            try:
                appt.google_event_id = calendar_service.create_booking_event(appt.patient_name, appt.phone, slot_iso)
            except Exception as e:
                logger.error(f"Failed to create Google Calendar event after payment: {e}")

        # Send immediate WhatsApp confirmation
        if appt.phone:
            conf_text = (
                f"✅ *Dr. Rao's Clinic - Advance Token Received*\n\n"
                f"Hello *{appt.patient_name}*, we received your UPI payment of *₹{amount_paid or settings.ADVANCE_TOKEN_AMOUNT_INR}* (Ref: {payment_id}).\n\n"
                f"📅 *Appointment*: {pretty_slot}\n"
                f"📍 *Clinic Address*: {settings.CLINIC_ADDRESS}\n"
                f"🗺️ *Google Maps*: {settings.CLINIC_MAPS_URL}\n\n"
                f"Your slot is confirmed. If you need to reschedule or cancel, reply to this message!"
            )
            background_tasks.add_task(send_message, appt.phone, conf_text)

    return {"status": "ok"}
