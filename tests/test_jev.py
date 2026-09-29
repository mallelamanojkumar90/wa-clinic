import os
import sys
# Hermetic test environment: use local SQLite and disable background scheduler
os.environ["DATABASE_URL"] = ""
os.environ["ENABLE_REMINDERS"] = "false"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app, process_patient_message
from app.jev_service import jev_service, TriageResult
from app.config import settings

client = TestClient(app)

def test_health_check_includes_jev_status():
    """Verify that root healthcheck reports Jev System-One Triage status."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "jev_system_one_triage" in data
    assert "/api/voice/triage" in data["voice_endpoints"]

def test_jev_emergency_triage_live_or_heuristic():
    """Test emergency detection for acute symptoms."""
    result = jev_service.triage_patient_message("I am having severe crushing chest pain and shortness of breath")
    assert result.is_emergency is True
    assert result.intent == "emergency"
    assert result.urgency == "urgent"
    assert result.should_bypass_llm is True

def test_jev_clinic_info_triage():
    """Test inquiry regarding clinic fees and operating hours."""
    result = jev_service.triage_patient_message("What are Dr. Rao's clinic timings and consultation fees?")
    assert result.is_emergency is False
    assert result.intent == "clinic_info"
    assert result.should_bypass_llm is True

def test_jev_check_slots_triage():
    """Test inquiry regarding slot availability."""
    result = jev_service.triage_patient_message("Are there any free appointment slots available tomorrow?")
    assert result.is_emergency is False
    assert result.intent == "check_slots"
    assert result.should_bypass_llm is True

def test_jev_multilingual_telugu():
    """Test Telugu emergency and slot queries."""
    # Emergency in Telugu
    em_result = jev_service.triage_patient_message("నాకు గుండె నొప్పిగా ఉంది, ఊపిరి ఆడటం లేదు")
    assert em_result.is_emergency is True

    # Slot request in Telugu / Tenglish
    slot_result = jev_service.triage_patient_message("Repu appointment slots unnaya?")
    assert slot_result.is_emergency is False

def test_jev_multilingual_hindi():
    """Test Hindi booking query."""
    result = jev_service.triage_patient_message("मुझे कल का अपॉइंटमेंट बुक करना है")
    assert result.is_emergency is False
    assert result.intent in ("book_appointment", "check_slots")

def test_voice_triage_endpoint():
    """Test /api/voice/triage endpoint for telephony callers (Bolna/Vobiz)."""
    # 1. Emergency caller
    resp = client.post(
        "/api/voice/triage",
        json={"utterance": "Help, patient has collapsed and is unconscious!", "phone": "+919876543210"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_emergency"] is True
    assert data["should_escalate_to_human"] is True
    assert data["suggested_action"] == "human_emergency_transfer"
    assert "1 0 8" in data["emergency_speech"]

    # 2. General appointment inquiry caller
    resp2 = client.post(
        "/api/voice/triage",
        json={"utterance": "I want to check what time doctor is available tomorrow", "phone": "+919876543210"}
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["is_emergency"] is False
    assert data2["should_escalate_to_human"] is False

@patch("app.main.send_message")
@patch("app.main.mark_as_read")
@patch("app.main.reply")
def test_webhook_emergency_fast_path_bypasses_llm(mock_reply, mock_read, mock_send):
    """Verify that acute medical emergencies trigger immediate life-safety guidance and bypass LLM."""
    test_phone = "+919999900001"
    emergency_text = "Severe chest pain and bleeding profusely"

    process_patient_message(test_phone, emergency_text, "msg_test_emerg_1")

    # LLM reply() must NOT be called
    mock_reply.assert_not_called()

    # WhatsApp message must be sent with emergency ambulance alert
    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][1]
    assert "URGENT MEDICAL NOTICE" in sent_text
    assert "108" in sent_text

@patch("app.main.send_message")
@patch("app.main.mark_as_read")
@patch("app.main.reply")
def test_webhook_clinic_info_fast_path_bypasses_llm(mock_reply, mock_read, mock_send):
    """Verify that pure clinic info queries return deterministic clinic info and bypass LLM."""
    test_phone = "+919999900002"
    info_text = "What is the clinic timing and address?"

    process_patient_message(test_phone, info_text, "msg_test_info_1")

    # LLM reply() must NOT be called
    mock_reply.assert_not_called()

    # WhatsApp message must be sent with clinic timings & fee
    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][1]
    assert "Dr. Rao's Clinic Information" in sent_text
    assert "10:00 AM" in sent_text
    assert "₹500" in sent_text

if __name__ == "__main__":
    print("Running test_health_check_includes_jev_status...")
    test_health_check_includes_jev_status()
    print("PASS")

    print("Running test_jev_emergency_triage_live_or_heuristic...")
    test_jev_emergency_triage_live_or_heuristic()
    print("PASS")

    print("Running test_jev_clinic_info_triage...")
    test_jev_clinic_info_triage()
    print("PASS")

    print("Running test_jev_check_slots_triage...")
    test_jev_check_slots_triage()
    print("PASS")

    print("Running test_jev_multilingual_telugu...")
    test_jev_multilingual_telugu()
    print("PASS")

    print("Running test_jev_multilingual_hindi...")
    test_jev_multilingual_hindi()
    print("PASS")

    print("Running test_voice_triage_endpoint...")
    test_voice_triage_endpoint()
    print("PASS")

    print("Running test_webhook_emergency_fast_path_bypasses_llm...")
    test_webhook_emergency_fast_path_bypasses_llm()
    print("PASS")

    print("Running test_webhook_clinic_info_fast_path_bypasses_llm...")
    test_webhook_clinic_info_fast_path_bypasses_llm()
    print("PASS")

    print("\nALL JEV TESTS PASSED SUCCESSFULLY!")
