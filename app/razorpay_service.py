"""Razorpay Payment Gateway integration for advance token / consultation payments."""
import hmac
import hashlib
import time
import logging
from typing import Optional, Dict, Any
import requests
from app.config import settings

logger = logging.getLogger(__name__)

RAZORPAY_API_URL = "https://api.razorpay.com/v1/payment_links"

class RazorpayService:
    def __init__(self):
        self.key_id = settings.RAZORPAY_KEY_ID
        self.key_secret = settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET

    @property
    def is_configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    def create_payment_link(
        self,
        appointment_id: int,
        patient_name: str,
        phone: str,
        amount_inr: int,
        slot_display: str,
        hold_minutes: int = 15
    ) -> Dict[str, Any]:
        """
        Generate a Razorpay Payment Link supporting UPI (Google Pay, PhonePe, Paytm),
        cards, and net banking with an automated expiration.
        """
        clean_phone = phone.lstrip("+")
        if not clean_phone.startswith("91") and len(clean_phone) == 10:
            formatted_contact = f"+91{clean_phone}"
        elif clean_phone.startswith("+"):
            formatted_contact = clean_phone
        else:
            formatted_contact = f"+{clean_phone}"

        # If Razorpay credentials are not yet set (or in test mode), return a simulated link
        if not self.is_configured:
            logger.warning("Razorpay credentials not configured. Generating simulated payment link for testing.")
            return {
                "id": f"plink_sim_{appointment_id}_{int(time.time())}",
                "short_url": f"https://rzp.io/i/sim_{appointment_id}",
                "amount": amount_inr,
                "simulated": True
            }

        expire_epoch = int(time.time()) + (hold_minutes * 60)
        payload = {
            "amount": amount_inr * 100,  # Razorpay expects amount in paise
            "currency": "INR",
            "accept_partial": False,
            "description": f"Dr. Rao Clinic Token: {slot_display}",
            "customer": {
                "name": patient_name or "Patient",
                "contact": formatted_contact
            },
            "notify": {
                "sms": False,
                "email": False
            },
            "reminder_enable": True,
            "expire_by": expire_epoch,
            "notes": {
                "appointment_id": str(appointment_id),
                "slot": slot_display,
                "phone": clean_phone
            }
        }

        try:
            response = requests.post(
                RAZORPAY_API_URL,
                auth=(self.key_id, self.key_secret),
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return {
                "id": data.get("id"),
                "short_url": data.get("short_url"),
                "amount": amount_inr,
                "simulated": False
            }
        except Exception as e:
            logger.error(f"Failed to create Razorpay payment link: {e}")
            # Fallback to simulated link to never block patient flow completely
            return {
                "id": f"plink_err_{appointment_id}",
                "short_url": f"https://rzp.io/i/err_{appointment_id}",
                "amount": amount_inr,
                "simulated": True,
                "error": str(e)
            }

    def verify_webhook_signature(self, raw_body: bytes, signature: Optional[str]) -> bool:
        """
        Verify that the webhook callback originated authentically from Razorpay.
        Uses HMAC-SHA256 signature verification.
        """
        if not self.webhook_secret:
            logger.warning("RAZORPAY_WEBHOOK_SECRET is not configured. Allowing webhook in dev/test mode.")
            return True

        if not signature:
            logger.warning("Missing X-Razorpay-Signature header.")
            return False

        try:
            expected_signature = hmac.new(
                self.webhook_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected_signature, signature)
        except Exception as e:
            logger.error(f"Error verifying Razorpay signature: {e}")
            return False

razorpay_service = RazorpayService()
