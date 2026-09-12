"""WhatsApp audio download and multi-lingual Whisper transcription service."""
import io
import logging
from typing import Optional
import requests
from openai import OpenAI
from app.config import settings

logger = logging.getLogger(__name__)
GRAPH_API_VERSION = "v21.0"

def download_whatsapp_media(media_id: str) -> Optional[bytes]:
    """Download audio/media binary from WhatsApp Cloud API using media ID."""
    if not settings.WHATSAPP_TOKEN or not media_id:
        logger.warning("Cannot download media: WHATSAPP_TOKEN or media_id is missing.")
        return None

    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
    }

    try:
        # Step 1: Query Meta Graph API for media URL
        info_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}"
        res = requests.get(info_url, headers=headers, timeout=10)
        if res.status_code != 200:
            logger.error(f"Failed to get media info for {media_id}: {res.status_code} {res.text}")
            return None

        media_info = res.json()
        download_url = media_info.get("url")
        if not download_url:
            logger.error(f"No download URL found in media info for {media_id}")
            return None

        # Step 2: Download binary data from media URL (requires Bearer auth)
        media_res = requests.get(download_url, headers=headers, timeout=20)
        if media_res.status_code == 200:
            logger.info(f"Successfully downloaded audio media {media_id} ({len(media_res.content)} bytes)")
            return media_res.content
        else:
            logger.error(f"Failed to download media content from {download_url}: {media_res.status_code}")
            return None

    except Exception as e:
        logger.error(f"Exception downloading WhatsApp media {media_id}: {e}")
        return None

def transcribe_audio(audio_bytes: bytes, filename: str = "voice_note.ogg") -> str:
    """Transcribe audio bytes (Telugu, Hindi, English, etc.) using Groq Whisper or OpenAI Whisper."""
    if not audio_bytes:
        return ""

    file_tuple = (filename, io.BytesIO(audio_bytes), "audio/ogg")

    # 1. Prefer Groq Whisper (ultra-fast whisper-large-v3-turbo)
    if settings.GROQ_API_KEY:
        try:
            logger.info("Transcribing audio via Groq Whisper (whisper-large-v3-turbo)...")
            client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=settings.GROQ_API_KEY
            )
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=file_tuple,
            )
            text = transcription.text.strip()
            logger.info(f"Groq transcription completed: '{text}'")
            return text
        except Exception as e:
            logger.error(f"Groq Whisper transcription failed: {e}")

    # 2. Fallback to OpenAI Whisper
    if settings.OPENAI_API_KEY:
        try:
            logger.info("Transcribing audio via OpenAI Whisper (whisper-1)...")
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=file_tuple,
            )
            text = transcription.text.strip()
            logger.info(f"OpenAI transcription completed: '{text}'")
            return text
        except Exception as e:
            logger.error(f"OpenAI Whisper transcription failed: {e}")

    # 3. Neither key configured
    logger.warning("No GROQ_API_KEY or OPENAI_API_KEY configured for Whisper audio transcription.")
    return ""
