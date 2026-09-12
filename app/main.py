"""FastAPI production entrypoint for WhatsApp Clinic Receptionist."""
import json
import logging
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Query, Depends, BackgroundTasks
from app.config import settings
from app.database import init_db, db_session
from app.models import ProcessedWebhook
from app.calendar_service import calendar_service
from app.security import verify_meta_signature
from app.agent import reply
from app.whatsapp import send_message, mark_as_read

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
    yield
    # Shutdown
    logger.info("Shutting down clinic receptionist service.")

app = FastAPI(
    title="Dr. Rao Clinic - WhatsApp AI Receptionist",
    description="Production-grade AI receptionist integrating WhatsApp Cloud API, Supabase, and Google Calendar.",
    version="2.0.0",
    lifespan=lifespan
)

@app.get("/")
def health_check():
    return {
        "status": "online",
        "service": "Dr. Rao Clinic WhatsApp AI Receptionist",
        "version": "2.0.0",
        "database": "postgresql (supabase)" if settings.is_postgres else "sqlite (local)",
        "google_calendar_sync": calendar_service.is_available,
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

def process_patient_message(phone: str, text: str, msg_id: str):
    """Background task to generate reply and send via WhatsApp."""
    try:
        # 1. Mark message as read
        mark_as_read(msg_id)

        # 2. Generate multi-lingual AI response
        logger.info(f"Generating AI reply for +{phone}: '{text}'")
        bot_reply = reply(text, phone)

        # 3. Deliver reply to WhatsApp
        send_message(phone, bot_reply)
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
                msg_type = msg.get("type")
                if msg_type == "text" and "text" in msg:
                    text = msg["text"].get("body", "").strip()
                elif "button" in msg:
                    text = msg["button"].get("text", "").strip()
                elif "interactive" in msg:
                    text = msg["interactive"].get("button_reply", {}).get("title", "").strip()

                if text:
                    logger.info(f"Received message from +{phone}: '{text}'")
                    # Offload to background task to respond instantly with HTTP 200 to Meta
                    background_tasks.add_task(process_patient_message, phone, text, msg_id)

    return {"ok": True}
