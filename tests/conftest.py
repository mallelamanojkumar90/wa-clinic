"""Pytest configuration and mocks for clinic tests.

Tests must never touch the live database, WhatsApp number, Google Calendar or payment
gateway configured in .env. Environment variables take priority over .env, so they are
overridden here before any app module is imported.
"""
import os
import sys
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="clinic-tests-")
os.environ.update({
    "APP_ENV": "development",
    "DATABASE_URL": "sqlite:///" + os.path.join(_TEST_DIR, "test.db").replace("\\", "/"),
    "WHATSAPP_TOKEN": "",
    "WHATSAPP_PHONE_ID": "",
    "VERIFY_TOKEN": "test-verify-token",
    "META_APP_SECRET": "",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "",
    "ENABLE_REMINDERS": "false",
    "RAZORPAY_KEY_ID": "",
    "RAZORPAY_KEY_SECRET": "",
    "RAZORPAY_WEBHOOK_SECRET": "",
    "VOICE_API_SECRET": "",
    "TYPESAFE_API_KEY": "",
    "GROQ_API_KEY": "",
    "OPENAI_API_KEY": "",
    "DASHBOARD_PIN": "1234",
})

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from unittest.mock import MagicMock
from app.calendar_service import calendar_service

@pytest.fixture(autouse=True)
def mock_google_calendar_for_tests(monkeypatch):
    """Belt and braces: never create or delete real Google Calendar events from tests."""
    monkeypatch.setattr(calendar_service, "create_booking_event", MagicMock(return_value="test_gcal_event_mock_123"))
    monkeypatch.setattr(calendar_service, "delete_booking_event", MagicMock(return_value=True))
    monkeypatch.setattr(calendar_service, "purge_orphaned_calendar_events", MagicMock(return_value=0))
