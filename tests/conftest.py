"""Pytest configuration and mocks for clinic tests."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from unittest.mock import MagicMock
from app.calendar_service import calendar_service

@pytest.fixture(autouse=True)
def mock_google_calendar_for_tests(monkeypatch):
    """
    Ensure automated test suites never insert test appointments into
    Dr. Rao's live Google Calendar or trigger phantom notifications.
    """
    monkeypatch.setattr(calendar_service, "create_booking_event", MagicMock(return_value="test_gcal_event_mock_123"))
    monkeypatch.setattr(calendar_service, "delete_booking_event", MagicMock(return_value=True))
    monkeypatch.setattr(calendar_service, "purge_orphaned_calendar_events", MagicMock(return_value=0))
