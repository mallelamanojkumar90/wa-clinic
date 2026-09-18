"""Background scheduler for automated appointment reminders (24h and 2h prior)."""
import logging
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from app.config import settings
from app.database import db_session
from app.models import Appointment
from app.whatsapp import send_message

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def _get_tz():
    return pytz.timezone(settings.CLINIC_TIMEZONE)

def _to_clinic_tz(d):
    if d is None:
        return None
    if d.tzinfo is None:
        d = pytz.utc.localize(d)
    return d.astimezone(_get_tz())

def check_and_send_reminders() -> int:
    """
    Scan booked appointments and send 24-hour and 2-hour reminders.
    Returns count of sent reminders.
    """
    tz = _get_tz()
    now = datetime.now(tz)
    reminders_sent = 0

    with db_session() as session:
        # Retrieve all active upcoming bookings
        booked_appts = session.query(Appointment).filter(
            Appointment.status == "booked",
            Appointment.slot > now
        ).all()

        for appt in booked_appts:
            if not appt.phone:
                continue

            local_slot = _to_clinic_tz(appt.slot)
            diff_hours = (local_slot - now).total_seconds() / 3600.0
            patient_name = appt.patient_name.strip() if appt.patient_name else "Patient"
            pretty_time = local_slot.strftime("%a %d %b, %I:%M %p")

            # 1. 24-hour reminder (within 2h to 24h window)
            if 2.0 < diff_hours <= 24.0 and not appt.reminder_24h_sent:
                text = (
                    f"⏰ *Dr. Rao's Clinic Reminder*\n\n"
                    f"Hello {patient_name}, you have an appointment with Dr. Rao tomorrow at *{pretty_time}*.\n\n"
                    f"📍 Dr. Rao's Clinic\n"
                    f"If you need to cancel or reschedule, simply reply to this message."
                )
                success = send_message(appt.phone, text)
                if success:
                    appt.reminder_24h_sent = True
                    reminders_sent += 1
                    logger.info(f"Sent 24h reminder to +{appt.phone} for {pretty_time}")

            # 2. 2-hour reminder (within 0h to 2h window)
            elif 0.0 < diff_hours <= 2.0 and not appt.reminder_2h_sent:
                text = (
                    f"🔔 *Dr. Rao's Clinic Reminder*\n\n"
                    f"Hello {patient_name}, your appointment with Dr. Rao is in about 2 hours at *{pretty_time}*.\n\n"
                    f"Please arrive 10 minutes prior to your consultation time. Have a safe journey!"
                )
                success = send_message(appt.phone, text)
                if success:
                    appt.reminder_2h_sent = True
                    reminders_sent += 1
                    logger.info(f"Sent 2h reminder to +{appt.phone} for {pretty_time}")

    return reminders_sent

def release_expired_holds() -> int:
    """
    Scan provisional holds (status="pending_payment") whose hold_expires_at is in the past.
    Reverts them to status="free" and notifies the patient on WhatsApp.
    Returns count of released slots.
    """
    tz = _get_tz()
    now = datetime.now(tz)
    released = 0

    with db_session() as session:
        expired_appts = session.query(Appointment).filter(
            Appointment.status == "pending_payment",
            Appointment.hold_expires_at.isnot(None),
            Appointment.hold_expires_at < now
        ).all()

        for appt in expired_appts:
            phone = appt.phone
            patient_name = appt.patient_name or "Patient"
            local_slot = _to_clinic_tz(appt.slot)
            pretty_time = local_slot.strftime("%a %d %b, %I:%M %p")

            # Revert slot to free
            appt.status = "free"
            appt.payment_status = "failed"
            appt.patient_name = ""
            appt.phone = ""
            appt.payment_link_id = None
            appt.payment_link_url = None
            appt.hold_expires_at = None
            released += 1

            logger.info(f"Released expired provisional hold for slot {pretty_time} (phone: +{phone})")

            # Send gentle notification to patient
            if phone:
                text = (
                    f"⌛ *Dr. Rao's Clinic - Reservation Expired*\n\n"
                    f"Hello {patient_name}, your provisional reservation for *{pretty_time}* expired as the advance payment was not completed.\n\n"
                    f"The slot has been released. If you would still like to consult Dr. Rao, simply reply to this message or call our clinic to book a new slot!"
                )
                try:
                    send_message(phone, text)
                except Exception as e:
                    logger.debug(f"Could not send hold expiration alert to +{phone}: {e}")

    return released

def start_scheduler():
    """Start the background appointment reminder scheduler."""
    if not settings.ENABLE_REMINDERS:
        logger.info("Automated appointment reminders are disabled in settings.")
        return

    if not scheduler.running:
        scheduler.add_job(
            check_and_send_reminders,
            "interval",
            minutes=settings.REMINDER_CHECK_INTERVAL_MINUTES,
            id="clinic_appointment_reminders",
            replace_existing=True
        )
        scheduler.add_job(
            release_expired_holds,
            "interval",
            minutes=2,
            id="clinic_release_expired_holds",
            replace_existing=True
        )
        scheduler.start()
        logger.info(f"Appointment reminder scheduler started (interval: {settings.REMINDER_CHECK_INTERVAL_MINUTES}m, hold cleaner: 2m)")

def stop_scheduler():
    """Gracefully shutdown background scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Appointment reminder scheduler stopped.")
