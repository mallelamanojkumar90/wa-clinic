"""Tests for Voice AI Receptionist endpoints and Razorpay UPI integration."""
import json
import hmac
import hashlib
from datetime import datetime, timedelta
import pytz
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.database import init_db, db_session
from app.models import Appointment
from app.tools import seed_slots_if_needed, get_free_slots, _to_clinic_tz, _get_tz
from app.razorpay_service import razorpay_service
from app.scheduler import release_expired_holds

init_db()
client = TestClient(app)

def test_health_check_voice_and_razorpay():
    """Verify health endpoint includes Voice AI and Razorpay status."""
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert "voice_ai_enabled" in data
    assert "razorpay_enabled" in data
    assert "/api/voice/slots" in data.get("voice_endpoints", [])

def test_voice_slots_endpoint():
    """Verify /api/voice/slots returns slots with spoken_text formatted for speech synthesis."""
    seed_slots_if_needed()
    res = client.get("/api/voice/slots")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "spoken_text" in data
    assert "Dr. Rao" in data["spoken_text"]
    assert len(data["slots"]) > 0

def test_voice_booking_direct_mode():
    """Test voice booking when advance payment is disabled (direct instant confirmation)."""
    settings.ENABLE_RAZORPAY = False
    seed_slots_if_needed()
    slots = get_free_slots()
    assert len(slots) > 0
    target_slot = slots[0].split(" ")[0]

    caller_phone = "+919811122233"
    caller_name = "Voice Caller One"

    res = client.post("/api/voice/book", json={
        "patient_name": caller_name,
        "phone": caller_phone,
        "slot": target_slot,
        "notes": "Consultation for seasonal allergies"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "confirmed"
    assert "confirmed" in data["spoken_text"].lower()

    # Verify in DB
    with db_session() as session:
        appt = session.query(Appointment).filter_by(phone="919811122233").first()
        assert appt is not None
        assert appt.status == "booked"
        assert appt.patient_name == caller_name

def test_voice_cancel():
    """Test cancelling an appointment via the voice endpoint."""
    res = client.post("/api/voice/cancel", json={"phone": "+919811122233"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cancelled"
    assert "cancelled" in data["spoken_text"].lower()

    with db_session() as session:
        appt = session.query(Appointment).filter_by(phone="919811122233").first()
        assert appt is None

def test_voice_booking_with_razorpay_hold():
    """Test voice booking when Razorpay advance token is required (provisional 15m hold)."""
    settings.ENABLE_RAZORPAY = True
    settings.ADVANCE_TOKEN_AMOUNT_INR = 200
    settings.SLOT_HOLD_MINUTES = 15

    seed_slots_if_needed()
    slots = get_free_slots()
    assert len(slots) > 0
    target_slot = slots[0].split(" ")[0]

    caller_phone = "+919833344455"
    caller_name = "Provisional Patient"

    res = client.post("/api/voice/book", json={
        "patient_name": caller_name,
        "phone": caller_phone,
        "slot": target_slot,
        "notes": "High fever"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending_payment"
    assert data["amount"] == 200
    assert "whatsapp" in data["spoken_text"].lower()
    assert "payment_url" in data

    # Verify provisional hold in DB
    with db_session() as session:
        appt = session.query(Appointment).filter_by(phone="919833344455").first()
        assert appt is not None
        assert appt.status == "pending_payment"
        assert appt.payment_status == "pending"
        assert appt.payment_amount == 200
        assert appt.hold_expires_at is not None

def test_razorpay_webhook_confirm_payment():
    """Test that incoming Razorpay webhook promotes provisional slot to booked."""
    with db_session() as session:
        appt = session.query(Appointment).filter_by(phone="919833344455").first()
        assert appt is not None
        appt_id = appt.id
        plink_id = appt.payment_link_id

    webhook_payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": plink_id or f"plink_test_{appt_id}",
                    "amount": 20000,
                    "amount_paid": 20000,
                    "status": "paid",
                    "notes": {
                        "appointment_id": str(appt_id),
                        "phone": "919833344455"
                    }
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_test_upi_12345",
                    "amount": 20000,
                    "status": "captured",
                    "method": "upi"
                }
            }
        }
    }
    raw_body = json.dumps(webhook_payload).encode("utf-8")

    # In test mode without secret, signature passes
    res = client.post(
        "/webhook/razorpay",
        content=raw_body,
        headers={"Content-Type": "application/json"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"

    # Verify appointment is now booked and paid
    with db_session() as session:
        confirmed = session.query(Appointment).filter_by(id=appt_id).first()
        assert confirmed.status == "booked"
        assert confirmed.payment_status == "paid"
        assert confirmed.hold_expires_at is None

def test_release_expired_holds():
    """Verify background task releases unpaid holds past their expiration."""
    tz = _get_tz()
    past_time = datetime.now(tz) - timedelta(minutes=20)

    with db_session() as session:
        expired_slot = session.query(Appointment).filter_by(status="free").first()
        assert expired_slot is not None
        expired_slot.patient_name = "Expired Patient"
        expired_slot.phone = "919999900000"
        expired_slot.status = "pending_payment"
        expired_slot.payment_status = "pending"
        expired_slot.hold_expires_at = past_time

    # Run release cleaner
    released_count = release_expired_holds()
    assert released_count >= 1

    # Verify slot is free again
    with db_session() as session:
        cleaned = session.query(Appointment).filter_by(phone="919999900000").first()
        assert cleaned is None  # Phone reset to empty
