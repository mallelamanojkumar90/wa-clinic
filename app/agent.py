"""Multi-lingual AI Receptionist Agent powered by OpenRouter / OpenAI."""
import json
import logging
from typing import List, Dict, Any
from openai import OpenAI
from app.config import settings
from app.database import db_session
from app.models import ChatMessage
from app.tools import TOOL_DEFINITIONS, get_free_slots, book_slot, cancel_booking

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the friendly, professional WhatsApp receptionist of Dr. Rao's Clinic (Operating Mon-Sat, 10:00 AM - 01:00 PM & 05:00 PM - 07:00 PM; Closed on Sundays).

Language Instructions:
1. MULTI-LINGUAL FLUENCY: You must seamlessly converse in whatever language the patient uses.
   - If the patient speaks Telugu (నమస్కారం) or Tenglish ("Repu appointment unda?"), reply warmly in Telugu or Tenglish.
   - If the patient speaks Hindi (नमस्ते) or Hinglish ("Kal ka slot mil sakta hai?"), reply in Hindi or Hinglish.
   - If the patient speaks English, reply in English.
   - If they switch languages, switch with them immediately.
2. TONE: Warm, concise, and helpful. Keep responses conversational and readable on mobile WhatsApp screens (use bullet points for dates/times).

Appointment Booking Rules:
- ALWAYS call the tool `get_free_slots` before stating slot availability to the patient. Never invent or hallucinate slots.
- To book an appointment, ask for the patient's full name and their preferred slot.
- Once the patient selects a slot, call `book_slot` with the verbatim slot string from `get_free_slots`, patient name, and phone number.
- To cancel an existing booking, call `cancel_booking` using their phone number.
"""

def _client() -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.OPENROUTER_API_KEY
    )

def get_chat_history(phone: str, limit: int = 10) -> List[Dict[str, str]]:
    """Retrieve persistent conversation history for a given phone number."""
    clean_phone = phone.lstrip("+")
    with db_session() as session:
        records = session.query(ChatMessage).filter(
            ChatMessage.phone == clean_phone
        ).order_by(ChatMessage.created_at.desc()).limit(limit).all()

        history = []
        for r in reversed(records):
            history.append({"role": r.role, "content": r.content})
        return history

def save_chat_message(phone: str, role: str, content: str):
    """Persist a single message to database history."""
    clean_phone = phone.lstrip("+")
    try:
        with db_session() as session:
            session.add(ChatMessage(phone=clean_phone, role=role, content=content))
    except Exception as e:
        logger.error(f"Failed to persist chat message: {e}")

def reply(user_text: str, phone: str) -> str:
    """Process incoming patient message and generate AI response with tool calling."""
    clean_phone = phone.lstrip("+")

    # Load persistent conversation history
    history = get_chat_history(clean_phone, limit=10)

    # Save incoming user message
    save_chat_message(clean_phone, "user", user_text)
    history.append({"role": "user", "content": user_text})

    system_msg = {
        "role": "system",
        "content": SYSTEM_PROMPT + f"\n\nCurrent Patient Phone: +{clean_phone}"
    }
    messages = [system_msg] + history[-10:]

    target_model = settings.MODEL.split("/", 1)[1] if "/" in settings.MODEL else settings.MODEL
    client = _client()

    for round_idx in range(4):  # Max 4 tool iterations
        try:
            response = client.chat.completions.create(
                model=target_model,
                messages=messages,
                tools=TOOL_DEFINITIONS
            )
        except Exception as e:
            logger.error(f"OpenRouter API call failed: {e}")
            return "Sorry, I am having trouble reaching our system. Please try again in a moment."

        choice = response.choices[0]
        msg = choice.message
        messages.append(msg)

        if not msg.tool_calls:
            assistant_reply = msg.content or ""
            save_chat_message(clean_phone, "assistant", assistant_reply)
            return assistant_reply

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}

            logger.info(f"[tool_call] {fn_name}({args})")

            tool_result = ""
            if fn_name == "get_free_slots":
                slots = get_free_slots()
                tool_result = str(slots)
            elif fn_name == "book_slot":
                tool_result = book_slot(**args)
            elif fn_name == "cancel_booking":
                target_phone = args.get("phone", clean_phone)
                tool_result = cancel_booking(target_phone)
            else:
                tool_result = f"Unknown tool {fn_name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(tool_result)
            })

    fallback_reply = "I apologize, but I could not complete that request. Please let me know how else I can help!"
    save_chat_message(clean_phone, "assistant", fallback_reply)
    return fallback_reply
