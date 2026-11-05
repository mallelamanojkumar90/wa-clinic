"""Voice speech synthesis utilities for Bolna.dev / Vobiz telephony integrations."""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from app.core.tools import _get_tz

def _format_slots_for_speech(slots: List[str], target_context: Optional[str] = None) -> str:
    """Transform ISO slots into natural, warm spoken English for Voice AI synthesis."""
    if not slots:
        if target_context == "today":
            return "Dr. Rao has no remaining slots available today. Would you like me to check upcoming openings for Monday?"
        elif target_context == "tomorrow":
            return "Dr. Rao's clinic is closed tomorrow on Sunday. The next available openings are on Monday. Would you like me to reserve a Monday slot?"
        return "Dr. Rao currently has no open slots available for the next few days. Would you like me to take a message for the front desk?"

    tz = _get_tz()
    now = datetime.now(tz)
    today_date = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()

    # Group slots by calendar date
    by_day: Dict[Any, List[str]] = {}
    for s in slots:
        # Expected: 2026-09-19T17:00:00 (Sat 19 Sep 05:00 PM)
        try:
            iso_part = s.split(" ")[0].strip()
            dt = datetime.fromisoformat(iso_part)
            if dt.tzinfo is None:
                dt = tz.localize(dt)
            else:
                dt = dt.astimezone(tz)
            d = dt.date()
            time_str = dt.strftime("%I:%M %p").lstrip("0")
            if d not in by_day:
                by_day[d] = []
            if time_str not in by_day[d]:
                by_day[d].append(time_str)
        except Exception:
            continue

    if not by_day:
        return "Dr. Rao has appointments available. Which time would you prefer?"

    phrases = []
    days_to_show = list(by_day.keys())[:2]
    for d in days_to_show:
        times = by_day[d]
        if d == today_date:
            day_label = "today"
        elif d == tomorrow_date:
            day_label = "tomorrow"
        else:
            day_label = d.strftime("on %A, %B %d").replace(" 0", " ")

        if len(times) == 1:
            times_formatted = times[0]
        elif len(times) == 2:
            times_formatted = f"{times[0]} and {times[1]}"
        else:
            times_formatted = f"{', '.join(times[:2])}, and {times[2]}"

        phrases.append(f"{day_label} at {times_formatted}")

    if len(phrases) == 1:
        return f"Dr. Rao is available {phrases[0]}. Which time works best for you?"
    else:
        return f"Dr. Rao is available {phrases[0]}, or {phrases[1]}. Which time would you prefer?"

def _clean_human_slot_labels(slots: List[str]) -> List[str]:
    """Convert raw slot strings into clean human phrases like 'Today at 6:00 PM'."""
    tz = _get_tz()
    now = datetime.now(tz)
    today_date = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()

    labels = []
    for s in slots:
        try:
            iso_part = s.split(" ")[0].strip()
            dt = datetime.fromisoformat(iso_part)
            if dt.tzinfo is None:
                dt = tz.localize(dt)
            else:
                dt = dt.astimezone(tz)
            d = dt.date()
            time_str = dt.strftime("%I:%M %p").lstrip("0")
            if d == today_date:
                labels.append(f"Today at {time_str}")
            elif d == tomorrow_date:
                labels.append(f"Tomorrow at {time_str}")
            else:
                labels.append(dt.strftime(f"%A %d %b at {time_str}").replace(" 0", " "))
        except Exception:
            labels.append(s)
    return labels
