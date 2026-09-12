"""WhatsApp Cloud API client for sending messages, receipts, and status updates."""
import logging
import requests
from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

def send_message(to: str, text: str) -> bool:
    """Send a plain text WhatsApp message."""
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        logger.warning(f"WhatsApp credentials not set. Simulated send to {to}: {text}")
        return True

    # Strip any leading '+' for Meta API
    recipient = to.lstrip("+")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"body": text},
    }

    try:
        res = requests.post(url, headers=_headers(), json=payload, timeout=10)
        if res.status_code == 200:
            logger.info(f"WhatsApp message successfully sent to +{recipient}")
            return True
        else:
            logger.error(f"WhatsApp send failed to +{recipient}: {res.status_code} {res.text}")
            return False
    except Exception as e:
        logger.error(f"Exception sending WhatsApp message to +{recipient}: {e}")
        return False

def mark_as_read(message_id: str) -> bool:
    """Mark an incoming message as read (shows blue double check marks to the patient)."""
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID or not message_id:
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    try:
        res = requests.post(url, headers=_headers(), json=payload, timeout=5)
        return res.status_code == 200
    except Exception as e:
        logger.debug(f"Could not mark message {message_id} as read: {e}")
        return False

def send_interactive_buttons(
    to: str,
    body_text: str,
    buttons: list,
    header_text: str = None,
    footer_text: str = None
) -> bool:
    """
    Send an interactive button reply message (up to 3 buttons).
    buttons: list of dicts with 'id' and 'title', e.g. [{"id": "btn_slots", "title": "📅 View Slots"}]
    """
    recipient = to.lstrip("+")
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        logger.warning(f"WhatsApp credentials not set. Simulated interactive buttons to +{recipient}: {[b['title'] for b in buttons]}")
        return True

    # Meta limits: maximum 3 buttons, title <= 20 chars
    action_buttons = []
    for b in buttons[:3]:
        btn_title = str(b.get("title", ""))[:20]
        btn_id = str(b.get("id", ""))[:256]
        action_buttons.append({
            "type": "reply",
            "reply": {
                "id": btn_id,
                "title": btn_title
            }
        })

    interactive_data = {
        "type": "button",
        "body": {"text": body_text[:1024]},
        "action": {"buttons": action_buttons}
    }
    if header_text:
        interactive_data["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive_data["footer"] = {"text": footer_text[:60]}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": interactive_data
    }

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"
    try:
        res = requests.post(url, headers=_headers(), json=payload, timeout=10)
        if res.status_code == 200:
            logger.info(f"Interactive buttons sent to +{recipient}")
            return True
        else:
            logger.error(f"Failed to send interactive buttons to +{recipient}: {res.status_code} {res.text}")
            return False
    except Exception as e:
        logger.error(f"Exception sending interactive buttons to +{recipient}: {e}")
        return False

def send_interactive_list(
    to: str,
    body_text: str,
    button_label: str,
    sections: list,
    header_text: str = None,
    footer_text: str = None
) -> bool:
    """
    Send an interactive list message (e.g. up to 10 appointment slots).
    button_label: Menu button text, e.g. "Select Slot" (max 20 chars)
    sections: list of dicts:
      [
        {
          "title": "Available Times",
          "rows": [
             {"id": "slot_iso_1", "title": "Mon 15 Sep 10:00 AM", "description": "Dr. Rao's Clinic"}
          ]
        }
      ]
    """
    recipient = to.lstrip("+")
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        logger.warning(f"WhatsApp credentials not set. Simulated interactive list to +{recipient}")
        return True

    # Format sections with Meta length constraints
    formatted_sections = []
    total_rows = 0
    for sec in sections:
        sec_title = str(sec.get("title", "Options"))[:24]
        sec_rows = []
        for r in sec.get("rows", []):
            if total_rows >= 10:
                break
            sec_rows.append({
                "id": str(r.get("id", f"row_{total_rows}"))[:200],
                "title": str(r.get("title", ""))[:24],
                "description": str(r.get("description", ""))[:72] if r.get("description") else ""
            })
            total_rows += 1
        if sec_rows:
            formatted_sections.append({
                "title": sec_title,
                "rows": sec_rows
            })
        if total_rows >= 10:
            break

    interactive_data = {
        "type": "list",
        "body": {"text": body_text[:1024]},
        "action": {
            "button": str(button_label)[:20],
            "sections": formatted_sections
        }
    }
    if header_text:
        interactive_data["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive_data["footer"] = {"text": footer_text[:60]}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": interactive_data
    }

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"
    try:
        res = requests.post(url, headers=_headers(), json=payload, timeout=10)
        if res.status_code == 200:
            logger.info(f"Interactive list sent to +{recipient}")
            return True
        else:
            logger.error(f"Failed to send interactive list to +{recipient}: {res.status_code} {res.text}")
            return False
    except Exception as e:
        logger.error(f"Exception sending interactive list to +{recipient}: {e}")
        return False

def send_slots_interactive_menu(to: str, slots: list, body_text: str = "Please choose your preferred appointment slot with Dr. Rao:") -> bool:
    """Send upcoming available slots as a native WhatsApp interactive list picker."""
    if not slots:
        return False

    rows = []
    for i, s in enumerate(slots[:10]):
        clean_s = str(s).strip()
        title = clean_s
        desc = "Doctor consultation"
        if "(" in clean_s and ")" in clean_s:
            inside = clean_s.split("(")[1].split(")")[0].strip()
            title = inside
            row_id = clean_s.split("(")[0].strip()
        else:
            row_id = f"slot_{i}"

        rows.append({
            "id": row_id,
            "title": title[:24],
            "description": desc[:72]
        })

    sections = [
        {
            "title": "Available Times",
            "rows": rows
        }
    ]
    return send_interactive_list(
        to=to,
        body_text=body_text,
        button_label="Select Slot",
        sections=sections,
        header_text="Dr. Rao's Clinic",
        footer_text="Tap to book instantly"
    )

def send_welcome_action_buttons(to: str, greeting: str = "Welcome to Dr. Rao's Clinic! How can I assist you today?") -> bool:
    """Send standard quick action reply buttons."""
    buttons = [
        {"id": "btn_view_slots", "title": "📅 View Slots"},
        {"id": "btn_cancel_booking", "title": "❌ Cancel Booking"},
        {"id": "btn_clinic_info", "title": "ℹ️ Clinic Timings"}
    ]
    return send_interactive_buttons(
        to=to,
        body_text=greeting,
        buttons=buttons,
        footer_text="Dr. Rao's Clinic"
    )
