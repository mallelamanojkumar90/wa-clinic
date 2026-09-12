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
