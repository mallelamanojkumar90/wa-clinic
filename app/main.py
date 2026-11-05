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

@asynccontextmanager
async def lifespan(app: FastAPI):
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

    if event in ("payment_link.paid", "payment.captured"):
        payload_data = data.get("payload", {})
        plink_entity = payload_data.get("payment_link", {}).get("entity", {})
        payment_entity = payload_data.get("payment", {}).get("entity", {})

        # Extract appointment ID or payment link ID
        notes = plink_entity.get("notes") or payment_entity.get("notes") or {}
        appt_id = notes.get("appointment_id")
        plink_id = plink_entity.get("id")
        payment_id = payment_entity.get("id") or "UPI_DIRECT"
        amount_paid = (plink_entity.get("amount_paid") or payment_entity.get("amount") or 0) // 100

        with db_session() as session:
            appt = None
            if appt_id:
                try:
                    appt = session.query(Appointment).filter_by(id=int(appt_id)).first()
                except (ValueError, TypeError):
                    pass
            if not appt and plink_id:
                appt = session.query(Appointment).filter_by(payment_link_id=plink_id).first()

            if not appt:
                logger.warning(f"No appointment found matching Razorpay webhook (appt_id={appt_id}, plink_id={plink_id})")
                return {"status": "ignored"}

            # Idempotency: if already confirmed and paid, don't duplicate calendar/messages
            if appt.status == "booked" and appt.payment_status == "paid":
                logger.info(f"Appointment {appt.id} already paid and booked. Skipping duplicate webhook.")
                return {"status": "already_processed"}

            # Promote from pending_payment to booked
            local_dt = _to_clinic_tz(appt.slot)
            pretty_slot = local_dt.strftime("%a %d %b at %I:%M %p")
            slot_iso = local_dt.strftime("%Y-%m-%dT%H:%M:%S")

            appt.status = "booked"
            appt.payment_status = "paid"
            appt.hold_expires_at = None

            # Sync with Google Calendar
            if calendar_service.is_available and not appt.google_event_id:
                try:
                    g_event_id = calendar_service.create_booking_event(appt.patient_name, appt.phone, slot_iso)
                    appt.google_event_id = g_event_id
                except Exception as e:
                    logger.error(f"Failed to create Google Calendar event after payment: {e}")

            # Send immediate WhatsApp confirmation
            if appt.phone:
                conf_text = (
                    f"✅ *Dr. Rao's Clinic - Advance Token Received*\n\n"
                    f"Hello *{appt.patient_name}*, we received your UPI payment of *₹{amount_paid or settings.ADVANCE_TOKEN_AMOUNT_INR}* (Ref: {payment_id}).\n\n"
                    f"📅 *Appointment*: {pretty_slot}\n"
                    f"📍 *Clinic Address*: Dr. Rao's Clinic, Road No. 36, Jubilee Hills, Hyderabad\n"
                    f"🗺️ *Google Maps*: https://maps.google.com/?q=Dr+Raos+Clinic+Hyderabad\n\n"
                    f"Your slot is confirmed. If you need to reschedule or cancel, reply to this message!"
                )
                background_tasks.add_task(send_message, appt.phone, conf_text)

    return {"status": "ok"}
