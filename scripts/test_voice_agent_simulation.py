"""Live end-to-end simulation of Bolna Voice AI Agent interactions."""
import os
import sys
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.database import db_session, init_db
from app.models import Appointment
from app.tools import seed_slots_if_needed, _to_clinic_tz

def print_banner(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def run_simulation():
    init_db()
    seed_slots_if_needed()
    client = TestClient(app)

    print_banner("1. HEALTH CHECK: VOICE AI & RAZORPAY STATUS")
    res = client.get("/")
    print("Health Status:", res.status_code)
    data = res.json()
    print("Service:", data.get("service"))
    print("Voice AI Enabled:", data.get("voice_ai_enabled"))
    print("Razorpay Enabled:", data.get("razorpay_enabled"))
    print("Voice Endpoints:", data.get("voice_endpoints"))

    print_banner("2. CALLER ASKS: 'What slots are available for Dr. Rao?'")
    res = client.get("/api/voice/slots")
    slots_data = res.json()
    print("Status Code:", res.status_code)
    print("Count of Slots Found:", slots_data.get("count"))
    print("First 3 Slots:", slots_data.get("slots")[:3])
    print("\n[Bolna TTS Voice Output to Caller]:")
    print(f"   \"{slots_data.get('spoken_text')}\"")

    if not slots_data.get("slots"):
        print("❌ No free slots available for testing.")
        return

    chosen_slot_raw = slots_data["slots"][0]
    chosen_slot = chosen_slot_raw.split(" ")[0]
    test_phone = "+919876543210"
    test_name = "Manoj Kumar"

    print_banner(f"3. CALLER SAYS: 'Book {chosen_slot} for Manoj Kumar'")
    print(f"Calling POST /api/voice/book for slot: {chosen_slot}...")
    
    # Test Mode 1: Free / Direct Confirmation (Default)
    settings.ENABLE_RAZORPAY = False
    book_payload = {
        "patient_name": test_name,
        "phone": test_phone,
        "slot": chosen_slot,
        "notes": "Patient has mild fever and cold"
    }
    res_book = client.post("/api/voice/book", json=book_payload)
    book_data = res_book.json()
    print("Status Code:", res_book.status_code)
    print("Booking Status:", book_data.get("status"))
    print("Appointment ID:", book_data.get("appointment_id"))
    print("\n[Bolna TTS Voice Output to Caller]:")
    print(f"   \"{book_data.get('spoken_text')}\"")

    # Verify Database state
    with db_session() as session:
        appt = session.query(Appointment).filter_by(phone="919876543210").first()
        print("\n[DB] [Database Verification]:")
        if appt:
            print(f"   Patient: {appt.patient_name}")
            print(f"   Phone: {appt.phone}")
            print(f"   Status: {appt.status}")
            print(f"   Slot: {_to_clinic_tz(appt.slot).strftime('%a %d %b %I:%M %p')}")
            print(f"   Google Calendar ID: {appt.google_event_id or 'DB Mode'}")
        else:
            print("   [ERROR] Appointment not found in DB!")

    print_banner("4. TESTING RAZORPAY UPI ADVANCE TOKEN FLOW (15-min hold)")
    settings.ENABLE_RAZORPAY = True
    settings.ADVANCE_TOKEN_AMOUNT_INR = 200

    # Pick another free slot for the Razorpay test
    res_slots2 = client.get("/api/voice/slots")
    slots2 = res_slots2.json().get("slots", [])
    if len(slots2) > 0:
        token_slot = slots2[0].split(" ")[0]
        token_phone = "+919811122233"
        token_patient = "Priya Sharma"

        res_token_book = client.post("/api/voice/book", json={
            "patient_name": token_patient,
            "phone": token_phone,
            "slot": token_slot,
            "notes": "Regular check-up"
        })
        token_data = res_token_book.json()
        print(f"Provisional Booking Status: {token_data.get('status')}")
        print(f"Amount: INR {token_data.get('amount')}")
        print(f"Generated UPI Payment URL: {token_data.get('payment_url')}")
        print("\n[Bolna TTS Voice Output mentioning WhatsApp UPI link]:")
        print(f"   \"{token_data.get('spoken_text')}\"")

        # Simulate Razorpay Webhook Callback
        print("\n[PAYMENT] Simulating Razorpay Webhook Callback (payment_link.paid)...")
        webhook_body = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_sim_test_123",
                        "amount": 20000,
                        "status": "paid",
                        "notes": {
                            "appointment_id": str(token_data.get("appointment_id")),
                            "phone": "919811122233"
                        }
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_live_upi_987654",
                        "amount": 20000,
                        "status": "captured",
                        "method": "upi"
                    }
                }
            }
        }
        res_webhook = client.post("/webhook/razorpay", json=webhook_body)
        print("Razorpay Webhook Response:", res_webhook.status_code, res_webhook.json())

        with db_session() as session:
            paid_appt = session.query(Appointment).filter_by(id=token_data.get("appointment_id")).first()
            if paid_appt:
                print(f"   Updated Status: {paid_appt.status}")
                print(f"   Payment Status: {paid_appt.payment_status}")
                print(f"   Hold Expired At: {paid_appt.hold_expires_at} (Cleared upon payment)")

    print_banner("5. CALLER ASKS TO CANCEL: 'Please cancel my appointment'")
    res_cancel = client.post("/api/voice/cancel", json={"phone": test_phone})
    cancel_data = res_cancel.json()
    print("Status Code:", res_cancel.status_code)
    print("Cancel Status:", cancel_data.get("status"))
    print("\n[Bolna TTS Voice Output to Caller]:")
    print(f"   \"{cancel_data.get('spoken_text')}\"")

    print_banner("[SUCCESS] VOICE AGENT SIMULATION COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run_simulation()
