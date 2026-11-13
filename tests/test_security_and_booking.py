"""Regression tests for webhook/voice/dashboard hardening and booking correctness."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.database import init_db, db_session
from app.core.models import Appointment
from app.core import tools
from app.core.tools import (
    _get_tz, _to_clinic_tz, seed_slots_if_needed, get_free_slots, book_slot, cancel_booking,
    match_requested_slot, claim_slot,
)
from app.core.scheduler import release_expired_holds
from app.channels.whatsapp import agent as wa_agent
from app.dashboard import router as dashboard_router

init_db()
client = TestClient(app)

PHONE_A = "919800000001"
PHONE_B = "919800000002"

@pytest.fixture(autouse=True)
def clean_test_rows():
    def wipe():
        with db_session() as s:
            s.query(Appointment).filter(Appointment.phone.in_([PHONE_A, PHONE_B])).delete(synchronize_session=False)
    wipe()
    yield
    wipe()

@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    yield

def _free_slot_row():
    seed_slots_if_needed()
    with db_session() as s:
        rows = tools.available_slot_rows(s)
        assert rows, "expected seeded free slots"
        return rows[0].id, _to_clinic_tz(rows[0].slot)

# -----------------------------------------------------------------------------
# Slot request parsing
# -----------------------------------------------------------------------------

def _fake_rows(now):
    tz = _get_tz()
    out = []
    for day in (1, 2):
        d = (now + timedelta(days=day)).date()
        for h in (10, 11, 12, 17, 18):
            out.append(SimpleNamespace(id=f"{d}-{h}", slot=tz.localize(datetime.combine(d, datetime.min.time()).replace(hour=h))))
    return out

def test_match_requested_slot_is_specific():
    tz = _get_tz()
    now = tz.localize(datetime(2026, 11, 13, 9, 0))  # Friday 13 Nov
    rows = _fake_rows(now)  # Sat 14 + Sun 15 (calendar-agnostic fake)

    def pick(text):
        m = match_requested_slot(text, rows, now)
        return None if m is None else _to_clinic_tz(m.slot).strftime("%d %H:%M")

    assert pick("2026-11-15T17:00:00 (Sun 15 Nov 05:00 PM)") == "15 17:00"
    assert pick("Sat 14 Nov 11:00 AM") == "14 11:00"
    assert pick("I want to book slot: Sun 15 Nov 06:00 PM (2026-11-15T18:00:00)") == "15 18:00"
    assert pick("tomorrow at 6 pm") == "14 18:00"
    assert pick("sunday 10am") == "15 10:00"
    assert pick("Nov 15 at 12") == "15 12:00"
    assert pick("6") == "14 18:00"  # bare hour -> clinic evening slot, earliest day
    # Previously "11" matched any slot whose ISO date contained "11" (every November slot)
    assert pick("11") == "14 11:00"
    # Not specific enough, or not a clinic time: never guess
    assert pick("") is None
    assert pick("tomorrow") is None
    assert pick("morning") is None
    assert pick("3 pm") is None

def test_book_slot_books_exactly_the_requested_slot():
    seed_slots_if_needed()
    slots = get_free_slots()
    target = slots[-1]  # deliberately not the first slot
    result = book_slot(target, "Patient A", PHONE_A)
    assert result.startswith("Booked Patient A")
    with db_session() as s:
        appt = s.query(Appointment).filter_by(phone=PHONE_A, status="booked").one()
        assert _to_clinic_tz(appt.slot).strftime("%Y-%m-%dT%H:%M:%S") == target.split(" ")[0]

def test_book_slot_requires_name_and_specific_time():
    seed_slots_if_needed()
    assert "full name" in book_slot(get_free_slots()[0], "  ", PHONE_A)
    assert "not available" in book_slot("tomorrow", "Patient A", PHONE_A)

def test_claim_slot_cannot_double_book():
    appt_id, _ = _free_slot_row()
    with db_session() as s:
        assert claim_slot(s, appt_id, patient_name="A", phone=PHONE_A, status="booked")
    with db_session() as s:
        assert not claim_slot(s, appt_id, patient_name="B", phone=PHONE_B, status="booked")
    with db_session() as s:
        assert s.query(Appointment).filter_by(id=appt_id).one().phone == PHONE_A

def test_rebooked_slot_gets_fresh_reminder_and_payment_state():
    appt_id, _ = _free_slot_row()
    with db_session() as s:
        appt = s.query(Appointment).filter_by(id=appt_id).one()
        appt.status = "cancelled"
        appt.phone = PHONE_A
        appt.reminder_24h_sent = True
        appt.reminder_2h_sent = True
        appt.payment_link_id = "plink_old"
        appt.payment_status = "failed"
    with db_session() as s:
        assert claim_slot(s, appt_id, patient_name="B", phone=PHONE_B, status="booked")
    with db_session() as s:
        appt = s.query(Appointment).filter_by(id=appt_id).one()
        assert appt.reminder_24h_sent is False and appt.reminder_2h_sent is False
        assert appt.payment_link_id is None and appt.payment_status == "none"

def test_cancel_only_affects_upcoming_booking():
    tz = _get_tz()
    with db_session() as s:
        s.add(Appointment(patient_name="Past", phone=PHONE_A, slot=datetime.now(tz) - timedelta(days=30), status="booked"))
    assert cancel_booking(PHONE_A) == "No active booking found for this phone number."

# -----------------------------------------------------------------------------
# WhatsApp agent: tools always act for the sender
# -----------------------------------------------------------------------------

def test_agent_tools_use_sender_phone_not_model_supplied_phone():
    with patch.object(wa_agent, "cancel_booking", return_value="ok") as cancel, \
         patch.object(wa_agent, "book_slot", return_value="ok") as book:
        wa_agent._run_tool("cancel_booking", {"phone": PHONE_B}, PHONE_A)
        cancel.assert_called_once_with(PHONE_A)
        wa_agent._run_tool("book_slot", {"slot": "x", "name": "N", "phone": PHONE_B, "extra": 1}, PHONE_A)
        book.assert_called_once_with(slot="x", name="N", phone=PHONE_A)
    # Malformed arguments become an error message instead of crashing the reply
    with patch.object(wa_agent, "book_slot", side_effect=TypeError("boom")):
        assert "failed" in wa_agent._run_tool("book_slot", {}, PHONE_A)
    tool_names = {t["function"]["name"]: t["function"]["parameters"] for t in tools.TOOL_DEFINITIONS}
    assert "phone" not in tool_names["book_slot"]["properties"]
    assert "phone" not in tool_names["cancel_booking"]["properties"]

# -----------------------------------------------------------------------------
# Payments
# -----------------------------------------------------------------------------

def _paid_webhook(appt_id, plink_id, phone):
    return json.dumps({
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {"entity": {"id": plink_id, "amount_paid": 20000,
                                        "notes": {"appointment_id": str(appt_id), "phone": phone}}},
            "payment": {"entity": {"id": "pay_test_1", "amount": 20000}},
        },
    }).encode()

def _hold(appt_id, phone, plink_id, expires_in_minutes=15):
    with db_session() as s:
        assert claim_slot(
            s, appt_id, patient_name="Holder", phone=phone, status="pending_payment",
            payment_status="pending", payment_link_id=plink_id,
            hold_expires_at=datetime.now(_get_tz()) + timedelta(minutes=expires_in_minutes),
        )

def test_payment_confirms_slot_still_held_by_that_link():
    appt_id, _ = _free_slot_row()
    _hold(appt_id, PHONE_A, "plink_ok")
    res = client.post("/webhook/razorpay", content=_paid_webhook(appt_id, "plink_ok", PHONE_A),
                      headers={"Content-Type": "application/json"})
    assert res.json()["status"] == "ok"
    with db_session() as s:
        appt = s.query(Appointment).filter_by(id=appt_id).one()
        assert (appt.status, appt.payment_status) == ("booked", "paid")
    # Razorpay retries are harmless
    res = client.post("/webhook/razorpay", content=_paid_webhook(appt_id, "plink_ok", PHONE_A),
                      headers={"Content-Type": "application/json"})
    assert res.json()["status"] == "already_processed"

def test_late_payment_never_takes_over_rebooked_slot():
    appt_id, _ = _free_slot_row()
    _hold(appt_id, PHONE_A, "plink_late", expires_in_minutes=-5)
    assert release_expired_holds() >= 1
    # Someone else books the released slot
    with db_session() as s:
        assert claim_slot(s, appt_id, patient_name="Second", phone=PHONE_B, status="booked")

    with patch("app.main.send_message") as send:
        res = client.post("/webhook/razorpay", content=_paid_webhook(appt_id, "plink_late", PHONE_A),
                          headers={"Content-Type": "application/json"})
    assert res.json()["status"] == "late_payment_refund_required"
    send.assert_called_once()
    assert send.call_args[0][0] == PHONE_A and "refunded" in send.call_args[0][1]
    with db_session() as s:
        appt = s.query(Appointment).filter_by(id=appt_id).one()
        assert (appt.phone, appt.status, appt.payment_status) == (PHONE_B, "booked", "none")

def test_unsigned_payment_webhook_rejected_in_production(production, monkeypatch):
    from app.core.razorpay_service import razorpay_service
    monkeypatch.setattr(razorpay_service, "webhook_secret", "")
    res = client.post("/webhook/razorpay", content=_paid_webhook(1, "plink_x", PHONE_A),
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 400

# -----------------------------------------------------------------------------
# Voice API and WhatsApp webhook authentication
# -----------------------------------------------------------------------------

def test_voice_api_requires_secret_in_production(production, monkeypatch):
    monkeypatch.setattr(settings, "VOICE_API_SECRET", None)
    assert client.post("/api/voice/cancel", json={"phone": PHONE_A}).status_code == 401
    monkeypatch.setattr(settings, "VOICE_API_SECRET", "s3cret")
    assert client.post("/api/voice/cancel", json={"phone": PHONE_A}).status_code == 401
    assert client.post("/api/voice/cancel", json={"phone": PHONE_A},
                       headers={"X-Voice-Secret": "wrong"}).status_code == 401
    assert client.post("/api/voice/cancel", json={"phone": PHONE_A},
                       headers={"X-Voice-Secret": "s3cret"}).status_code == 200

def test_voice_api_disabled_when_voice_ai_off(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_VOICE_AI", False)
    assert client.get("/api/voice/slots").status_code == 404

def test_unsigned_whatsapp_webhook_rejected_in_production(production, monkeypatch):
    monkeypatch.setattr(settings, "META_APP_SECRET", None)
    assert client.post("/webhook", json={"entry": []}).status_code == 403

def test_webhook_handshake_rejects_empty_token(monkeypatch):
    monkeypatch.setattr(settings, "VERIFY_TOKEN", "")
    res = client.get("/webhook?hub.mode=subscribe&hub.challenge=abc&hub.verify_token=")
    assert res.status_code == 403

# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------

def test_dashboard_rejects_pin_in_query_string():
    c = TestClient(app)
    assert c.get(f"/api/dashboard/appointments?pin={settings.DASHBOARD_PIN}").status_code == 401

def test_dashboard_locks_out_after_repeated_wrong_pins(monkeypatch):
    monkeypatch.setattr(dashboard_router, "_failed_attempts", {})
    c = TestClient(app)
    for _ in range(dashboard_router.MAX_FAILED_ATTEMPTS):
        assert c.post("/api/dashboard/login", json={"pin": "0000"}).status_code == 401
    # Even the right PIN is refused during the lockout
    assert c.post("/api/dashboard/login", json={"pin": settings.DASHBOARD_PIN}).status_code == 429

def test_dashboard_disabled_with_default_pin_in_production(production, monkeypatch):
    monkeypatch.setattr(settings, "DASHBOARD_PIN", "1234")
    c = TestClient(app)
    assert c.post("/api/dashboard/login", json={"pin": "1234"}).status_code == 503
    monkeypatch.setattr(settings, "DASHBOARD_PIN", "strong-pin-42")
    assert c.post("/api/dashboard/login", json={"pin": "strong-pin-42"}).status_code == 200

def test_dashboard_session_invalidated_when_pin_changes(monkeypatch):
    c = TestClient(app)
    assert c.post("/api/dashboard/login", json={"pin": settings.DASHBOARD_PIN}).status_code == 200
    assert c.get("/api/dashboard/appointments").status_code == 200
    monkeypatch.setattr(settings, "DASHBOARD_PIN", "a-new-pin-99")
    assert c.get("/api/dashboard/appointments").status_code == 401

def test_dashboard_walkin_cannot_overwrite_existing_booking():
    appt_id, _ = _free_slot_row()
    with db_session() as s:
        assert claim_slot(s, appt_id, patient_name="Existing", phone=PHONE_A, status="booked")
    res = client.post("/api/dashboard/book",
                      json={"appointment_id": appt_id, "patient_name": "Walk In", "phone": PHONE_B},
                      headers={"X-Dashboard-PIN": settings.DASHBOARD_PIN})
    assert res.status_code == 409

def test_dashboard_page_escapes_patient_data():
    html = client.get("/dashboard").text
    assert "esc(appt.patient_name)" in html and "esc(appt.notes)" in html
