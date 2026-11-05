"""WhatsApp Channel Router: Meta Webhook verification, incoming message handling, and interactive dispatch."""
import json
import logging
from typing import Optional
from fastapi import APIRouter, Request, Response, Query, Depends, BackgroundTasks

from app.core.config import settings
from app.core.database import db_session
from app.core.models import ProcessedWebhook
from app.core.tools import get_free_slots
from app.core.jev_service import jev_service
from app.channels.whatsapp.security import verify_meta_signature
from app.channels.whatsapp.agent import reply, save_chat_message
from app.channels.whatsapp.client import send_message, mark_as_read, send_slots_interactive_menu, send_welcome_action_buttons
from app.channels.whatsapp.transcription import download_whatsapp_media, transcribe_audio

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WhatsApp AI Receptionist"])

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
    import sys
    main_mod = sys.modules.get("app.main")
    _send_msg = getattr(main_mod, "send_message", send_message) if main_mod else send_message
    _mark_read = getattr(main_mod, "mark_as_read", mark_as_read) if main_mod else mark_as_read
    _reply = getattr(main_mod, "reply", reply) if main_mod else reply

    try:
        # 1. Mark message as read (shows blue double ticks on WhatsApp)
        _mark_read(msg_id)

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
                        _send_msg(
                            phone,
                            "Thank you for contacting Dr. Rao's Clinic! Voice note processing is currently in setup. Please type your message in Telugu, Hindi, or English!"
                        )
                    else:
                        _send_msg(
                            phone,
                            "Sorry, I could not understand the voice note clearly. Please send it again or type your message."
                        )
                    return
            else:
                _send_msg(
                    phone,
                    "Sorry, I was unable to download your voice message. Please send it again or type your query."
                )
                return

        if not text:
            return

        # 3. TypeSafe Jev System-One Decision Triage & Fast Routing (70-150ms)
        triage = jev_service.triage_patient_message(text)
        logger.info(
            f"[Jev Triage] +{phone} (source={triage.source}): "
            f"emergency={triage.is_emergency}, intent={triage.intent}, urgency={triage.urgency}"
        )

        # 3a. Emergency Fast-Path (Life-safety protocol - immediately bypasses LLM)
        if triage.is_emergency or triage.intent == "emergency":
            emergency_reply = (
                "⚠️ *URGENT MEDICAL NOTICE / అత్యవసర నోటీసు*\n\n"
                "If you or the patient are experiencing a life-threatening medical emergency "
                "(e.g., severe chest pain, breathing difficulty, uncontrollable bleeding, stroke symptoms, or sudden collapse):\n\n"
                "🚨 **IMMEDIATELY CALL 108 (Ambulance)** or go to the nearest Hospital Emergency Room.\n\n"
                "మీరు లేదా రోగి తీవ్రమైన అత్యవసర పరిస్థితిలో ఉంటే, వెంటనే **108 (అంబులెన్స్)** కి కాల్ చేయండి లేదా దగ్గరలోని ఎమర్జెన్సీ ఆసుపత్రికి వెళ్లండి.\n\n"
                "🏥 Dr. Rao's Clinic Emergency Desk: +91 98765 43210"
            )
            save_chat_message(phone, "assistant", emergency_reply)
            _send_msg(phone, emergency_reply)
            return

        # Check if text contains Indic scripts (Telugu, Devanagari) or conversational intent
        has_indic_script = any('\u0c00' <= char <= '\u0c7f' or '\u0900' <= char <= '\u097f' for char in text)
        is_direct_button_or_static_info = text in (
            "What are the clinic timings and consultation details?",
            "What is the clinic timing and address?"
        ) or (triage.intent == "clinic_info" and not has_indic_script and not any(w in text.lower() for w in ["book", "cancel", "slot", "repu", "kal", "dr", "doctor", "consultation"]))

        # 3b. Clinic Information Fast-Path (Deterministic info for static info queries / buttons)
        if is_direct_button_or_static_info:
            info_reply = (
                "🏥 *Dr. Rao's Clinic Information*\n\n"
                "⏰ *Timings*: Mon–Sat: 10:00 AM – 01:00 PM & 05:00 PM – 07:00 PM (Closed Sundays)\n"
                "🩺 *Consultation Fee*: ₹500 (Advance Token: ₹200 via UPI)\n"
                "📍 *Address*: Dr. Rao's Clinic, Main Road, Hyderabad\n\n"
                "Would you like to check today's or tomorrow's available appointment slots?"
            )
            save_chat_message(phone, "assistant", info_reply)
            _send_msg(phone, info_reply)
            send_welcome_action_buttons(phone, "Quick options:")
            return

        # 3c. Pure Slot Inquiry Fast-Path (For direct WhatsApp button clicks like "Show available appointment slots")
        if text.strip() == "Show available appointment slots":
            free_slots = get_free_slots()
            if free_slots:
                slots_text = (
                    "📅 *Available Appointment Slots:*\n\n"
                    + "\n".join([f"• {s}" for s in free_slots[:5]])
                    + "\n\nPlease select a slot from the list below or reply with your preferred time and full name:"
                )
                save_chat_message(phone, "assistant", slots_text)
                _send_msg(phone, slots_text)
                try:
                    send_slots_interactive_menu(phone, free_slots[:10])
                except Exception as e:
                    logger.debug(f"Could not send interactive slots menu: {e}")
                return

        # 4. Handle Greeting with Quick Action Buttons
        is_greeting = text.lower().strip() in ("hi", "hello", "hey", "namaste", "namaskaram", "namaskaramu", "start")
        if is_greeting:
            bot_reply = _reply(text, phone, triage=triage)
            _send_msg(phone, bot_reply)
            send_welcome_action_buttons(phone, "Quick options:")
            return

        # 5. Generate multi-lingual AI response (enriched with Jev System-One Triage)
        logger.info(f"Generating AI reply for +{phone}: '{text}' [Triage: {triage.intent}/{triage.urgency}]")
        bot_reply = _reply(text, phone, triage=triage)

        # 6. Deliver reply to WhatsApp
        _send_msg(phone, bot_reply)

        # 7. If user inquired about slots, also deliver interactive slot selection menu
        asked_slots = (triage.intent == "check_slots" or any(w in text.lower() for w in ["slot", "available", "schedule", "free", "appointment timings", "repu", "kal"])) and "cancel" not in text.lower()
        if asked_slots:
            try:
                free_slots = get_free_slots()
                if free_slots:
                    send_slots_interactive_menu(phone, free_slots[:10])
            except Exception as e:
                logger.debug(f"Could not send interactive slots menu: {e}")

    except Exception as e:
        logger.error(f"Error processing message from +{phone}: {e}")

@router.get("/webhook")
@router.get("/api/whatsapp/webhook")
def meta_webhook_verification(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Meta Developer Webhook Verification Handshake."""
    # Friendly response if opened directly in a browser without Meta handshake params
    if not hub_mode and not hub_verify_token:
        return {
            "status": "ready",
            "service": "WhatsApp Cloud API Webhook Listener",
            "message": "This endpoint is live and waiting for Meta's verification handshake.",
            "usage": "Paste this URL into Meta Developer Dashboard -> WhatsApp -> Configuration -> Callback URL, with your configured Verify Token."
        }

    logger.info(f"Received webhook verification handshake: mode={hub_mode}")
    configured_token = (settings.VERIFY_TOKEN or "").strip()
    received_token = (hub_verify_token or "").strip()

    if hub_mode == "subscribe" and received_token == configured_token:
        logger.info("Webhook verification successful. Echoing challenge.")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed: token mismatch.")
    return Response(content="Verification token mismatch", status_code=403)

@router.post("/webhook")
@router.post("/api/whatsapp/webhook")
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

            # Log message delivery statuses (sent, delivered, read, failed)
            statuses = value.get("statuses", [])
            for st in statuses:
                recipient = st.get("recipient_id")
                status_val = st.get("status")
                wamid = st.get("id")
                errors = st.get("errors")
                if errors:
                    for err in errors:
                        err_code = err.get("code")
                        err_title = err.get("title")
                        err_msg = err.get("message")
                        err_details = err.get("error_data", {}).get("details", "")
                        logger.error(
                            f"[Meta Webhook Status] Message {wamid} to +{recipient} FAILED: "
                            f"Code {err_code} - {err_title}: {err_msg} ({err_details})"
                        )
                else:
                    logger.info(f"[Meta Webhook Status] Message {wamid} to +{recipient} -> {status_val}")

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
