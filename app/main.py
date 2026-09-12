"""FastAPI production entrypoint for WhatsApp Clinic Receptionist."""
import json
import logging
import sys
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Query, Depends, BackgroundTasks
from app.config import settings
from app.database import init_db, db_session
from app.models import ProcessedWebhook
from app.calendar_service import calendar_service
from app.security import verify_meta_signature
from app.agent import reply
from app.whatsapp import send_message, mark_as_read, send_slots_interactive_menu, send_welcome_action_buttons
from app.transcription import download_whatsapp_media, transcribe_audio
from app.scheduler import start_scheduler, stop_scheduler
from app.tools import get_free_slots

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("clinic_receptionist")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database schema
    logger.info("Initializing database tables...")
    init_db()
    logger.info(f"Database ready: {'PostgreSQL (Supabase)' if settings.is_postgres else 'SQLite (Local)'}")
    logger.info(f"Google Calendar status: {'Active' if calendar_service.is_available else 'Disabled (using DB slots)'}")

    # Start automated appointment reminder scheduler
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()
    logger.info("Shutting down clinic receptionist service.")

app = FastAPI(
    title="Dr. Rao Clinic - WhatsApp AI Receptionist",
    description="Production-grade AI receptionist integrating WhatsApp Cloud API, Supabase, Google Calendar, Voice Notes, and Interactive Messages.",
    version="2.1.0",
    lifespan=lifespan
)

@app.get("/")
def health_check():
    return {
        "status": "online",
        "service": "Dr. Rao Clinic WhatsApp AI Receptionist",
        "version": "2.1.0",
        "database": "postgresql (supabase)" if settings.is_postgres else "sqlite (local)",
        "google_calendar_sync": calendar_service.is_available,
        "reminders_enabled": settings.ENABLE_REMINDERS,
        "audio_transcription": bool(settings.GROQ_API_KEY or settings.OPENAI_API_KEY),
        "model": settings.MODEL
    }

@app.get("/webhook")
def meta_webhook_verification(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Meta Developer Webhook Verification Handshake."""
    logger.info(f"Received webhook verification handshake: mode={hub_mode}")
    if hub_mode == "subscribe" and hub_verify_token == settings.VERIFY_TOKEN:
        logger.info("Webhook verification successful. Echoing challenge.")
        return Response(content=hub_challenge, media_type="text/plain")
    logger.warning("Webhook verification failed: token mismatch.")
    return Response(content="Verification token mismatch", status_code=403)

def is_duplicate_webhook(wamid: str, phone: str) -> bool:
    """Check if message ID was already processed; store it if new."""
    if not wamid:
        return False
    with db_session() as session:
        existing = session.query(ProcessedWebhook).filter_by(wamid=wamid).first()
        if existing:
            return True
        session.add(ProcessedWebhook(wamid=wamid, phone=phone))
        return False

def process_patient_message(phone: str, text: str, msg_id: str, audio_id: Optional[str] = None):
    """Background task to generate reply and send via WhatsApp."""
    try:
        # 1. Mark message as read
        mark_as_read(msg_id)

        # 2. If voice note, download and transcribe
        if audio_id:
            logger.info(f"Downloading and transcribing voice note {audio_id} from +{phone}")
            audio_bytes = download_whatsapp_media(audio_id)
            if audio_bytes:
                transcription = transcribe_audio(audio_bytes)
                if transcription:
                    text = transcription
                    logger.info(f"Voice note successfully transcribed: '{text}'")
                else:
                    if not settings.GROQ_API_KEY and not settings.OPENAI_API_KEY:
                        send_message(
                            phone,
                            "Thank you for contacting Dr. Rao's Clinic! Voice note processing is currently in setup. Please type your message in Telugu, Hindi, or English!"
                        )
                    else:
                        send_message(
                            phone,
                            "Sorry, I could not understand the voice note clearly. Please send it again or type your message."
                        )
                    return
            else:
                send_message(
                    phone,
                    "Sorry, I was unable to download your voice message. Please send it again or type your query."
                )
                return

        if not text:
            return

        # 3. Handle Greeting with Quick Action Buttons
        is_greeting = text.lower().strip() in ("hi", "hello", "hey", "namaste", "namaskaram", "namaskaramu", "start")
        if is_greeting:
            bot_reply = reply(text, phone)
            send_message(phone, bot_reply)
            send_welcome_action_buttons(phone, "Quick options:")
            return

        # 4. Generate multi-lingual AI response
        logger.info(f"Generating AI reply for +{phone}: '{text}'")
        bot_reply = reply(text, phone)

        # 5. Deliver reply to WhatsApp
        send_message(phone, bot_reply)

        # 6. If user inquired about slots, also deliver interactive slot selection menu
        asked_slots = any(w in text.lower() for w in ["slot", "available", "schedule", "free", "appointment timings", "repu", "kal"]) and "cancel" not in text.lower()
        if asked_slots:
            try:
                free_slots = get_free_slots()
                if free_slots:
                    send_slots_interactive_menu(phone, free_slots[:10])
            except Exception as e:
                logger.debug(f"Could not send interactive slots menu: {e}")

    except Exception as e:
        logger.error(f"Error processing message from +{phone}: {e}")

@app.post("/webhook")
async def meta_webhook_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    raw_body: bytes = Depends(verify_meta_signature)
):
    """Incoming WhatsApp message webhook handler."""
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        logger.error(f"Invalid JSON payload: {e}")
        return {"ok": True}

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            for msg in messages:
                phone = msg.get("from")
                msg_id = msg.get("id", "")
                if not phone:
                    continue

                # Idempotency check: skip already processed messages
                if is_duplicate_webhook(msg_id, phone):
                    logger.info(f"Skipping duplicate webhook message {msg_id}")
                    continue

                text = ""
                audio_id = None
                msg_type = msg.get("type")

                if msg_type == "text" and "text" in msg:
                    text = msg["text"].get("body", "").strip()
                elif msg_type in ("audio", "voice"):
                    audio_info = msg.get("audio") or msg.get("voice") or {}
                    audio_id = audio_info.get("id")
                elif "button" in msg:
                    text = msg["button"].get("text", "").strip()
                elif "interactive" in msg:
                    interactive = msg.get("interactive", {})
                    int_type = interactive.get("type")
                    if int_type == "button_reply":
                        btn_reply = interactive.get("button_reply", {})
                        btn_id = btn_reply.get("id", "")
                        btn_title = btn_reply.get("title", "")
                        if btn_id == "btn_view_slots":
                            text = "Show available appointment slots"
                        elif btn_id == "btn_cancel_booking":
                            text = "I want to cancel my booking"
                        elif btn_id == "btn_clinic_info":
                            text = "What are the clinic timings and consultation details?"
                        else:
                            text = btn_title
                    elif int_type == "list_reply":
                        list_reply = interactive.get("list_reply", {})
                        row_id = list_reply.get("id", "")
                        row_title = list_reply.get("title", "")
                        text = f"I want to book slot: {row_title} ({row_id})"

                if text or audio_id:
                    logger.info(f"Queuing processing for +{phone} (text='{text}', audio_id={audio_id})")
                    # Offload to background task to respond instantly with HTTP 200 to Meta
                    background_tasks.add_task(process_patient_message, phone, text, msg_id, audio_id)

    return {"ok": True}
