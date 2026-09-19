"""Google Calendar integration for Dr. Rao's clinic.
Synchronizes free/busy slot queries and creates/deletes calendar events.
"""
import json
import logging
import os
from datetime import datetime, timedelta, time
from typing import List, Optional, Tuple
import pytz

from app.config import settings

logger = logging.getLogger(__name__)

# Clinic standard hours (Monday to Saturday)
CLINIC_SLOT_HOURS = (10, 11, 12, 17, 18)  # 10am-1pm, 5pm-7pm
CLINIC_DAYS_OF_WEEK = (0, 1, 2, 3, 4, 5)  # Mon-Sat (0=Mon, 5=Sat)

class GoogleCalendarService:
    def __init__(self):
        self._service = None
        self._tz = pytz.timezone(settings.CLINIC_TIMEZONE)
        self._init_service()

    def _init_service(self):
        if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            logger.info("GOOGLE_SERVICE_ACCOUNT_JSON not provided. Using database slot manager.")
            return

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            raw_creds = settings.GOOGLE_SERVICE_ACCOUNT_JSON.strip()
            # Check if it's a file path or direct JSON string
            if os.path.exists(raw_creds):
                creds = service_account.Credentials.from_service_account_file(
                    raw_creds, scopes=["https://www.googleapis.com/auth/calendar"]
                )
            else:
                info = json.loads(raw_creds)
                creds = service_account.Credentials.from_service_account_info(
                    info, scopes=["https://www.googleapis.com/auth/calendar"]
                )

            self._service = build("calendar", "v3", credentials=creds)
            logger.info("Google Calendar service successfully initialized.")
        except Exception as e:
            logger.warning(f"Failed to initialize Google Calendar client: {e}. Falling back to DB slots.")
            self._service = None

    @property
    def is_available(self) -> bool:
        return self._service is not None

    def get_free_slots(self, days_ahead: int = 3) -> List[Tuple[str, str]]:
        """Query Google Calendar free/busy to find unoccupied clinic slots.
        Returns list of (iso_slot, display_str).
        """
        if not self.is_available:
            return []

        now = datetime.now(self._tz)
        start_time = now.isoformat()
        end_time = (now + timedelta(days=days_ahead + 1)).replace(hour=23, minute=59, second=59).isoformat()

        try:
            body = {
                "timeMin": start_time,
                "timeMax": end_time,
                "timeZone": settings.CLINIC_TIMEZONE,
                "items": [{"id": settings.GOOGLE_CALENDAR_ID}],
            }
            res = self._service.freebusy().query(body=body).execute()
            busy_periods = res.get("calendars", {}).get(settings.GOOGLE_CALENDAR_ID, {}).get("busy", [])

            # Parse busy intervals
            busy_ranges = []
            for b in busy_periods:
                b_start = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(self._tz)
                b_end = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(self._tz)
                busy_ranges.append((b_start, b_end))

            free_slots = []
            # Generate expected clinic operating slots (starting from today if slots remain)
            for day_offset in range(0, days_ahead + 1):
                target_date = (now + timedelta(days=day_offset)).date()
                if target_date.weekday() not in CLINIC_DAYS_OF_WEEK:
                    continue  # Clinic closed on Sundays

                for hour in CLINIC_SLOT_HOURS:
                    slot_dt = self._tz.localize(datetime.combine(target_date, time(hour, 0)))
                    slot_end = slot_dt + timedelta(minutes=45)

                    if slot_dt <= now:
                        continue

                    # Check collision with busy ranges
                    is_busy = False
                    for b_start, b_end in busy_ranges:
                        if not (slot_end <= b_start or slot_dt >= b_end):
                            is_busy = True
                            break

                    if not is_busy:
                        iso_str = slot_dt.strftime("%Y-%m-%dT%H:%M:%S")
                        display_str = f'{iso_str} ({slot_dt.strftime("%a %d %b %I:%M %p")})'
                        free_slots.append((iso_str, display_str))

            return free_slots
        except Exception as e:
            logger.error(f"Error querying Google Calendar free/busy: {e}")
            return []

    def create_booking_event(self, name: str, phone: str, slot_iso: str, duration_minutes: int = 45) -> Optional[str]:
        """Create an event on Dr. Rao's Google Calendar. Returns event_id if successful."""
        if not self.is_available:
            return None

        try:
            slot_dt = datetime.fromisoformat(slot_iso)
            if slot_dt.tzinfo is None:
                slot_dt = self._tz.localize(slot_dt)
            end_dt = slot_dt + timedelta(minutes=duration_minutes)

            event = {
                "summary": f"Clinic Appointment - {name}",
                "description": f"Patient Name: {name}\nPhone: +{phone}\nBooked via WhatsApp AI Receptionist",
                "start": {"dateTime": slot_dt.isoformat(), "timeZone": settings.CLINIC_TIMEZONE},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": settings.CLINIC_TIMEZONE},
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 30},
                    ],
                },
            }

            created_event = self._service.events().insert(
                calendarId=settings.GOOGLE_CALENDAR_ID, body=event
            ).execute()
            logger.info(f"Google Calendar event created: {created_event.get('id')}")
            return created_event.get("id")
        except Exception as e:
            logger.error(f"Failed to create Google Calendar event: {e}")
            return None

    def delete_booking_event(self, event_id: str) -> bool:
        """Remove appointment event from Google Calendar."""
        if not self.is_available or not event_id:
            return False

        try:
            self._service.events().delete(
                calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id
            ).execute()
            logger.info(f"Deleted Google Calendar event: {event_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete Google Calendar event {event_id}: {e}")
            return False

calendar_service = GoogleCalendarService()
