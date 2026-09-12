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
