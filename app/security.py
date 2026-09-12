"""Meta WhatsApp Webhook security and HMAC signature verification."""
import hmac
import hashlib
import logging
from typing import Optional
from fastapi import Request, HTTPException, status
from app.config import settings

logger = logging.getLogger(__name__)

async def verify_meta_signature(request: Request) -> bytes:
    """Verify that incoming POST request genuinely originates from Meta using App Secret.
    Returns raw body bytes for JSON parsing.
    """
    body = await request.body()

    # If META_APP_SECRET is not configured, pass through (e.g. local development)
    if not settings.META_APP_SECRET:
        return body

    signature_header = request.headers.get("X-Hub-Signature-256")
    if not signature_header:
        logger.warning("Missing X-Hub-Signature-256 header on incoming webhook request.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing signature header"
        )

    parts = signature_header.split("sha256=", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signature format"
        )

    expected_hash = parts[1]
    computed_hash = hmac.new(
        key=settings.META_APP_SECRET.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, computed_hash):
        logger.error("Meta HMAC signature mismatch! Rejected unauthorized request.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signature verification failed"
        )

    return body
