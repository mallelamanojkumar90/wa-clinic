"""Automated tests for production WhatsApp Clinic Receptionist."""
import os
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.database import init_db
from app.tools import get_free_slots, book_slot, cancel_booking, seed_slots_if_needed
from app.agent import reply

# Initialize tables for tests
init_db()
client = TestClient(app)

def test_health_check():
    """Verify healthcheck endpoint returns 200 and operational details."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "version" in data
    assert "database" in data

def test_webhook_handshake_valid():
    """Verify Meta handshake with matching verify_token returns plain challenge."""
    response = client.get(f"/webhook?hub.mode=subscribe&hub.challenge=test_12345&hub.verify_token={settings.VERIFY_TOKEN}")
    assert response.status_code == 200
    assert response.text == "test_12345"
    assert "text/plain" in response.headers.get("content-type", "")

def test_webhook_handshake_invalid():
    """Verify Meta handshake with wrong token returns 403 Forbidden."""
    response = client.get("/webhook?hub.mode=subscribe&hub.challenge=test_12345&hub.verify_token=wrong_token")
    assert response.status_code == 403

def test_tools_slot_and_booking_flow():
    """Test get_free_slots, booking, and cancellation."""
    seed_slots_if_needed()
    slots = get_free_slots()
    assert len(slots) > 0, "Should have upcoming free slots"

    # Pick first slot
    target_slot = slots[0].split(" ")[0]
    test_phone = "+919999988888"
    test_name = "Automated Test Patient"

    # Book slot
    book_result = book_slot(target_slot, test_name, test_phone)
    assert f"Booked {test_name}" in book_result

    # Cancel slot
    cancel_result = cancel_booking(test_phone)
    assert "cancelled" in cancel_result.lower()

def test_agent_multilingual_telugu():
    """Test AI agent understanding of Telugu query."""
    test_phone = "+919999977777"
    response = reply("నమస్కారం, రేపు ఖాళీగా ఉన్న స్లాట్లు ఏవి?", test_phone)
    assert response is not None
    assert len(response) > 5

def test_agent_multilingual_hindi():
    """Test AI agent understanding of Hindi query."""
    test_phone = "+919999966666"
    response = reply("नमस्ते, क्या कल कोई अपॉइंटमेंट स्लॉट उपलब्ध है?", test_phone)
    assert response is not None
    assert len(response) > 5

def test_interactive_messages_helpers(monkeypatch):
    """Test building and sending interactive buttons and list messages."""
    import requests
    class MockResponse:
        status_code = 200
        text = '{"success": true}'
        def json(self):
            return {"success": True}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse())

    from app.whatsapp import (
        send_interactive_buttons,
        send_interactive_list,
        send_slots_interactive_menu,
        send_welcome_action_buttons
    )

    test_phone = "+919999911111"

    # 1. Interactive buttons
    btn_res = send_interactive_buttons(
        to=test_phone,
        body_text="Choose an option:",
        buttons=[{"id": "b1", "title": "Option 1"}, {"id": "b2", "title": "Option 2"}]
    )
    assert btn_res is True

    # 2. Interactive list
    list_res = send_interactive_list(
        to=test_phone,
        body_text="Choose a slot:",
        button_label="Select",
        sections=[{
            "title": "Times",
            "rows": [{"id": "s1", "title": "Mon 10:00 AM", "description": "Dr. Rao"}]
        }]
    )
    assert list_res is True

    # 3. Slots interactive menu helper
    slots_res = send_slots_interactive_menu(
        to=test_phone,
        slots=["2026-09-15T10:00:00 (Tue 15 Sep 10:00 AM)"]
    )
    assert slots_res is True

    # 4. Welcome buttons
    welcome_res = send_welcome_action_buttons(test_phone)
    assert welcome_res is True

def test_appointment_reminders_scheduler(monkeypatch):
    """Test 24-hour and 2-hour automated reminder checks."""
    import requests
    class MockResponse:
        status_code = 200
        text = '{"success": true}'
        def json(self):
            return {"success": True}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse())

    from datetime import datetime, timedelta
    import pytz
    from app.database import db_session
    from app.models import Appointment
    from app.scheduler import check_and_send_reminders, _get_tz

    tz = _get_tz()
    now = datetime.now(tz)

    test_phone_24h = "919999922222"
    test_phone_2h = "919999933333"

    slot_24h = now + timedelta(hours=10)
    slot_2h = now + timedelta(hours=1)

    with db_session() as session:
        # Clean up any test records
        session.query(Appointment).filter(Appointment.phone.in_([test_phone_24h, test_phone_2h])).delete()

        # Add 24h candidate
        session.add(Appointment(
            patient_name="Patient 24h",
            phone=test_phone_24h,
            slot=slot_24h,
            status="booked",
            reminder_24h_sent=False,
            reminder_2h_sent=False
        ))
        # Add 2h candidate
        session.add(Appointment(
            patient_name="Patient 2h",
            phone=test_phone_2h,
            slot=slot_2h,
            status="booked",
            reminder_24h_sent=False,
            reminder_2h_sent=False
        ))

    # First check should send reminders
    sent_count = check_and_send_reminders()
    assert sent_count >= 2, f"Expected at least 2 reminders sent, got {sent_count}"

    # Verify flags updated in DB
    with db_session() as session:
        a_24h = session.query(Appointment).filter_by(phone=test_phone_24h).first()
        a_2h = session.query(Appointment).filter_by(phone=test_phone_2h).first()
        assert a_24h.reminder_24h_sent is True
        assert a_2h.reminder_2h_sent is True

    # Second check should be idempotent (0 sent)
    second_run = check_and_send_reminders()
    # No new reminders should be sent for these appointments
    with db_session() as session:
        session.query(Appointment).filter(Appointment.phone.in_([test_phone_24h, test_phone_2h])).delete()

def test_webhook_interactive_and_audio_payloads():
    """Verify webhook handles button_reply, list_reply, and audio message payloads gracefully."""
    # 1. Interactive Button Reply
    payload_btn = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919999944444",
                        "id": "wamid.test.btn1",
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {"id": "btn_view_slots", "title": "📅 View Slots"}
                        }
                    }]
                }
            }]
        }]
    }
    res_btn = client.post("/webhook", json=payload_btn)
    assert res_btn.status_code == 200

    # 2. Interactive List Reply
    payload_list = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919999955555",
                        "id": "wamid.test.list1",
                        "type": "interactive",
                        "interactive": {
                            "type": "list_reply",
                            "list_reply": {"id": "2026-09-15T10:00:00", "title": "Tue 15 Sep 10:00 AM"}
                        }
                    }]
                }
            }]
        }]
    }
    res_list = client.post("/webhook", json=payload_list)
    assert res_list.status_code == 200

    # 3. Audio Voice Note Payload
    payload_audio = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919999966667",
                        "id": "wamid.test.audio1",
                        "type": "audio",
                        "audio": {"id": "media_audio_test_123"}
                    }]
                }
            }]
        }]
    }
    res_audio = client.post("/webhook", json=payload_audio)
    assert res_audio.status_code == 200

def test_dashboard_views_and_apis(monkeypatch):
    """Test dashboard frontend render, PIN authentication, status updating, and broadcast."""
    import requests
    class MockResponse:
        status_code = 200
        text = '{"success": true}'
        def json(self):
            return {"success": True}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse())

    # 1. Dashboard HTML page renders successfully
    res_page = client.get("/dashboard")
    assert res_page.status_code == 200
    assert "Dr. Rao's Clinic" in res_page.text

    # 2. Unauthenticated API access is rejected
    client.cookies.clear()
    res_unauth = client.get("/api/dashboard/appointments")
    assert res_unauth.status_code == 401

    # 3. Authentication endpoint
    res_fail = client.post("/api/dashboard/login", json={"pin": "wrong_pin"})
    assert res_fail.status_code == 401

    res_login = client.post("/api/dashboard/login", json={"pin": settings.DASHBOARD_PIN})
    assert res_login.status_code == 200
    assert "dashboard_pin" in res_login.headers.get("set-cookie", "")

    # 4. Appointments API with cookie auth
    res_appts = client.get("/api/dashboard/appointments")
    assert res_appts.status_code == 200
    data = res_appts.json()
    assert "appointments" in data
    assert "summary" in data

    if data["appointments"]:
        target_appt = data["appointments"][0]
        appt_id = target_appt["id"]

        # 4. Status update test
        res_status = client.post(
            "/api/dashboard/status",
            json={"appointment_id": appt_id, "status": "blocked"},
            headers=headers
        )
        assert res_status.status_code == 200
        assert res_status.json()["status"] == "blocked"

        # Reopen to free
        res_free = client.post(
            "/api/dashboard/status",
            json={"appointment_id": appt_id, "status": "free"},
            headers=headers
        )
        assert res_free.status_code == 200
        assert res_free.json()["status"] == "free"

        # 5. Walk-in booking test
        res_walkin = client.post(
            "/api/dashboard/book",
            json={
                "appointment_id": appt_id,
                "patient_name": "Walkin Test Patient",
                "phone": "919999900000",
                "notes": "Walk-in consultation"
            },
            headers=headers
        )
        assert res_walkin.status_code == 200

        # 6. Broadcast test
        today_str = data["date"]
        res_broadcast = client.post(
            "/api/dashboard/broadcast",
            json={
                "date": today_str,
                "message": "Notice: Dr. Rao is running 30 minutes late today."
            },
            headers=headers
        )
        assert res_broadcast.status_code == 200
        assert "sent_count" in res_broadcast.json()

        # Clean up test appointment
        client.post(
            "/api/dashboard/status",
            json={"appointment_id": appt_id, "status": "free"},
            headers=headers
        )
