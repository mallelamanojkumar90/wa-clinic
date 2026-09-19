"""Clinic Front-Desk Live Web Dashboard Router and UI.

Provides real-time patient queue management, status updating, walk-in bookings,
slot blocking, and emergency WhatsApp delay broadcasting.
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List
import pytz
from fastapi import APIRouter, Request, Response, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from app.config import settings
from app.database import db_session
from app.models import Appointment
from app.calendar_service import calendar_service
from app.whatsapp import send_message
from app.tools import seed_slots_if_needed, _to_clinic_tz

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

def _get_tz():
    return pytz.timezone(settings.CLINIC_TIMEZONE)

def is_authenticated(request: Request) -> bool:
    """Verify request has valid dashboard authentication PIN."""
    # Check header, cookie, or query param
    pin = (
        request.headers.get("X-Dashboard-PIN")
        or request.cookies.get("dashboard_pin")
        or request.query_params.get("pin")
    )
    return pin == settings.DASHBOARD_PIN

def require_auth(request: Request):
    """FastAPI dependency to protect dashboard API endpoints."""
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing dashboard PIN"
        )

# Pydantic Schemas
class LoginRequest(BaseModel):
    pin: str

class StatusUpdateRequest(BaseModel):
    appointment_id: int
    status: str  # 'completed', 'cancelled', 'blocked', 'free'

class WalkinBookingRequest(BaseModel):
    appointment_id: int
    patient_name: str
    phone: str
    notes: Optional[str] = None

class BroadcastRequest(BaseModel):
    date: str  # 'YYYY-MM-DD'
    message: str

# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------

@router.post("/api/dashboard/login")
def dashboard_login(payload: LoginRequest, response: Response):
    """Authenticate dashboard using configured DASHBOARD_PIN."""
    if payload.pin.strip() == settings.DASHBOARD_PIN.strip():
        # Set persistent cookie valid for 7 days
        response.set_cookie(
            key="dashboard_pin",
            value=settings.DASHBOARD_PIN,
            max_age=604800,
            httponly=False,
            samesite="lax"
        )
        return {"ok": True, "message": "Authenticated"}
    raise HTTPException(status_code=401, detail="Incorrect PIN")

@router.get("/api/dashboard/appointments")
def get_daily_appointments(
    request: Request,
    target_date: Optional[str] = None,
    _: None = Depends(require_auth)
):
    """Fetch all appointments & slots for a selected date with live metrics."""
    tz = _get_tz()
    seed_slots_if_needed()

    if target_date:
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            parsed_date = datetime.now(tz).date()
    else:
        parsed_date = datetime.now(tz).date()

    start_dt = tz.localize(datetime.combine(parsed_date, datetime.min.time()))
    end_dt = tz.localize(datetime.combine(parsed_date, datetime.max.time()))

    with db_session() as session:
        records = session.query(Appointment).filter(
            Appointment.slot >= start_dt,
            Appointment.slot <= end_dt
        ).order_by(Appointment.slot.asc()).all()

        items = []
        counts = {"total": 0, "booked": 0, "completed": 0, "free": 0, "blocked": 0, "pending_payment": 0, "cancelled": 0}

        for appt in records:
            local_dt = _to_clinic_tz(appt.slot)
            time_display = local_dt.strftime("%I:%M %p")
            item_status = (appt.status or "free").lower()

            counts["total"] += 1
            if item_status in counts:
                counts[item_status] += 1

            items.append({
                "id": appt.id,
                "time": time_display,
                "iso": local_dt.isoformat(),
                "patient_name": appt.patient_name or "",
                "phone": appt.phone or "",
                "status": item_status,
                "payment_status": appt.payment_status or "none",
                "payment_amount": appt.payment_amount,
                "payment_link_url": appt.payment_link_url or "",
                "notes": appt.notes or "",
                "google_event_id": appt.google_event_id or ""
            })

    return {
        "date": parsed_date.strftime("%Y-%m-%d"),
        "date_display": parsed_date.strftime("%A, %d %B %Y"),
        "summary": counts,
        "appointments": items
    }

@router.post("/api/dashboard/status")
def update_appointment_status(
    payload: StatusUpdateRequest,
    _: None = Depends(require_auth)
):
    """Update appointment status (completed, cancelled, blocked, free) & sync Calendar."""
    new_status = payload.status.lower().strip()
    if new_status not in ("completed", "cancelled", "blocked", "free", "booked"):
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

    with db_session() as session:
        appt = session.query(Appointment).filter_by(id=payload.appointment_id).first()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")

        old_status = appt.status

        # If transitioning to cancelled or free, remove associated Google Calendar event
        if new_status in ("cancelled", "free") and appt.google_event_id and calendar_service.is_available:
            try:
                calendar_service.delete_booking_event(appt.google_event_id)
            except Exception as e:
                logger.error(f"Error removing calendar event: {e}")
            appt.google_event_id = None

        if new_status == "free":
            appt.patient_name = ""
            appt.phone = ""
        elif new_status == "cancelled":
            appt.notes = f"{appt.notes or ''} [Cancelled via Front-Desk]".strip()

        appt.status = new_status
        session.commit()

        logger.info(f"Dashboard updated appt #{appt.id} status: {old_status} -> {new_status}")
        return {"ok": True, "id": appt.id, "status": appt.status}

@router.post("/api/dashboard/book")
def walkin_booking(
    payload: WalkinBookingRequest,
    _: None = Depends(require_auth)
):
    """Manual walk-in or reception phone booking."""
    with db_session() as session:
        appt = session.query(Appointment).filter_by(id=payload.appointment_id).first()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment slot not found")

        clean_phone = payload.phone.lstrip("+")
        local_dt = _to_clinic_tz(appt.slot)
        iso_str = local_dt.strftime("%Y-%m-%dT%H:%M:%S")

        # Sync with Google Calendar if enabled
        g_event_id = None
        if calendar_service.is_available:
            try:
                g_event_id = calendar_service.create_booking_event(
                    payload.patient_name,
                    clean_phone,
                    iso_str
                )
            except Exception as e:
                logger.error(f"Failed to sync walk-in booking to Google Calendar: {e}")

        appt.patient_name = payload.patient_name.strip()
        appt.phone = clean_phone
        appt.notes = payload.notes.strip() if payload.notes else "Walk-in booking"
        appt.status = "booked"
        appt.google_event_id = g_event_id
        session.commit()

        logger.info(f"Dashboard created walk-in booking for {appt.patient_name} at {iso_str}")
        return {"ok": True, "message": f"Booked {appt.patient_name}"}

@router.post("/api/dashboard/broadcast")
def broadcast_delay_alert(
    payload: BroadcastRequest,
    _: None = Depends(require_auth)
):
    """Send delay or announcement WhatsApp message to all patients booked for a given date."""
    try:
        target_date = datetime.strptime(payload.date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD")

    tz = _get_tz()
    start_dt = tz.localize(datetime.combine(target_date, datetime.min.time()))
    end_dt = tz.localize(datetime.combine(target_date, datetime.max.time()))

    with db_session() as session:
        # Retrieve all currently booked appointments for this date
        booked_appts = session.query(Appointment).filter(
            Appointment.slot >= start_dt,
            Appointment.slot <= end_dt,
            Appointment.status == "booked"
        ).all()

        sent_count = 0
        recipients = []

        for appt in booked_appts:
            if not appt.phone:
                continue
            recipients.append(appt.phone)
            # Dispatch WhatsApp alert
            try:
                send_message(appt.phone, payload.message.strip())
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to +{appt.phone}: {e}")

    logger.info(f"Delay broadcast sent to {sent_count}/{len(recipients)} patients for {payload.date}")
    return {
        "ok": True,
        "sent_count": sent_count,
        "total_recipients": len(recipients)
    }

# -----------------------------------------------------------------------------
# Frontend HTML Dashboard
# -----------------------------------------------------------------------------

@router.get("/dashboard", response_class=HTMLResponse)
def render_dashboard(request: Request):
    """Serve the single-page responsive Clinic Reception Dashboard."""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dr. Rao's Clinic - Live Reception Dashboard</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        clinic: {
                            50: '#f0f9ff',
                            100: '#e0f2fe',
                            500: '#0ea5e9',
                            600: '#0284c7',
                            700: '#0369a1',
                            900: '#0c4a6e',
                        }
                    }
                }
            }
        }
    </script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        [x-cloak] { display: none !important; }
    </style>
</head>
<body class="bg-slate-50 text-slate-800 antialiased min-h-screen flex flex-col">

    <!-- Top Navigation Bar -->
    <header class="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-sm">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-clinic-600 text-white flex items-center justify-center font-bold text-xl shadow-md shadow-clinic-500/20">
                    <i class="fa-solid fa-user-doctor"></i>
                </div>
                <div>
                    <h1 class="text-lg font-bold text-slate-900 leading-tight">Dr. Rao's Clinic</h1>
                    <p class="text-xs text-slate-500 font-medium">Front-Desk & Live Queue Dashboard</p>
                </div>
            </div>

            <div class="flex items-center gap-3">
                <!-- Status Badge -->
                <span class="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    Live Sync Active
                </span>
                <!-- Refresh Button -->
                <button onclick="loadAppointments()" title="Refresh Data" class="p-2 rounded-lg text-slate-600 hover:bg-slate-100 hover:text-slate-900 border border-slate-200 transition">
                    <i class="fa-solid fa-arrows-rotate" id="refresh-icon"></i>
                </button>
                <!-- Lock / Logout -->
                <button onclick="logout()" title="Logout" class="p-2 rounded-lg text-rose-600 hover:bg-rose-50 border border-slate-200 transition">
                    <i class="fa-solid fa-right-from-bracket"></i>
                </button>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 flex-1 w-full space-y-6">

        <!-- Controls & Date Selection Header -->
        <div class="bg-white p-4 sm:p-5 rounded-2xl border border-slate-200 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div class="flex flex-wrap items-center gap-3">
                <label for="date-picker" class="text-sm font-semibold text-slate-700 flex items-center gap-2">
                    <i class="fa-regular fa-calendar text-clinic-600"></i> Date:
                </label>
                <input type="date" id="date-picker" onchange="loadAppointments()" class="px-3.5 py-2 rounded-xl border border-slate-300 text-sm font-semibold text-slate-900 focus:outline-none focus:ring-2 focus:ring-clinic-500/20 focus:border-clinic-500 transition shadow-sm">
                <button onclick="setDateToday()" class="px-3 py-2 rounded-xl text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 transition">
                    Today
                </button>
                <button onclick="setDateOffset(1)" class="px-3 py-2 rounded-xl text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 transition">
                    Tomorrow
                </button>
            </div>

            <!-- Quick Action Buttons -->
            <div class="flex items-center gap-2.5">
                <button onclick="openBroadcastModal()" class="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-amber-500 hover:bg-amber-600 text-white shadow-sm transition">
                    <i class="fa-solid fa-bullhorn"></i>
                    <span>Delay Broadcast</span>
                </button>
            </div>
        </div>

        <!-- KPI Metrics Grid -->
        <div class="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
                <p class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Today's Total</p>
                <p class="text-2xl font-bold text-slate-900 mt-1" id="stat-total">0</p>
                <span class="text-xs text-slate-400 font-medium">Slots allocated</span>
            </div>
            <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
                <p class="text-xs font-semibold text-emerald-600 uppercase tracking-wider">Confirmed Bookings</p>
                <p class="text-2xl font-bold text-emerald-600 mt-1" id="stat-booked">0</p>
                <span class="text-xs text-slate-400 font-medium">Waiting in queue</span>
            </div>
            <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
                <p class="text-xs font-semibold text-clinic-600 uppercase tracking-wider">Completed</p>
                <p class="text-2xl font-bold text-clinic-600 mt-1" id="stat-completed">0</p>
                <span class="text-xs text-slate-400 font-medium">Finished consultations</span>
            </div>
            <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
                <p class="text-xs font-semibold text-rose-600 uppercase tracking-wider">Cancelled</p>
                <p class="text-2xl font-bold text-rose-600 mt-1" id="stat-cancelled">0</p>
                <span class="text-xs text-slate-400 font-medium">Cancelled bookings</span>
            </div>
            <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
                <p class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Free Available</p>
                <p class="text-2xl font-bold text-slate-700 mt-1" id="stat-free">0</p>
                <span class="text-xs text-slate-400 font-medium">Open for WhatsApp bot</span>
            </div>
        </div>

        <!-- Appointment Queue Table -->
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
            <div class="p-5 border-b border-slate-100 flex items-center justify-between">
                <div>
                    <h2 class="text-base font-bold text-slate-900" id="schedule-title">Patient Queue</h2>
                    <p class="text-xs text-slate-500">Live view of clinic consultation schedule</p>
                </div>
                <div id="loading-indicator" class="hidden text-clinic-600 text-sm font-medium flex items-center gap-2">
                    <i class="fa-solid fa-circle-notch fa-spin"></i> Loading...
                </div>
            </div>

            <div class="overflow-x-auto">
                <table class="w-full text-left text-sm text-slate-700">
                    <thead class="bg-slate-50 text-xs font-semibold text-slate-500 uppercase border-b border-slate-200">
                        <tr>
                            <th class="px-6 py-3.5">Time Slot</th>
                            <th class="px-6 py-3.5">Patient Name</th>
                            <th class="px-6 py-3.5">WhatsApp / Phone</th>
                            <th class="px-6 py-3.5">Status</th>
                            <th class="px-6 py-3.5">Notes</th>
                            <th class="px-6 py-3.5 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="appointments-body" class="divide-y divide-slate-100 font-medium">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- Modal: Login Modal -->
    <div id="login-modal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-white rounded-2xl shadow-xl max-w-sm w-full p-6 text-center space-y-4 border border-slate-200">
            <div class="w-12 h-12 rounded-2xl bg-clinic-100 text-clinic-600 mx-auto flex items-center justify-center text-xl">
                <i class="fa-solid fa-lock"></i>
            </div>
            <div>
                <h3 class="text-lg font-bold text-slate-900">Dr. Rao's Clinic Access</h3>
                <p class="text-xs text-slate-500 mt-1">Enter clinic passcode / PIN to access live dashboard</p>
            </div>
            <form onsubmit="handleLogin(event)" class="space-y-4">
                <div>
                    <input type="password" id="login-pin" maxlength="10" placeholder="Enter PIN (Default: 1234)" class="w-full text-center tracking-widest text-lg px-4 py-2.5 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-clinic-500/20 focus:border-clinic-500">
                    <p id="login-error" class="text-xs text-rose-600 font-semibold mt-1 hidden"></p>
                </div>
                <button type="submit" class="w-full py-2.5 rounded-xl bg-clinic-600 hover:bg-clinic-700 text-white font-semibold text-sm shadow-md shadow-clinic-500/20 transition">
                    Unlock Dashboard
                </button>
            </form>
        </div>
    </div>

    <!-- Modal: Walk-in Manual Booking -->
    <div id="walkin-modal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 space-y-4 border border-slate-200">
            <div class="flex items-center justify-between border-b border-slate-100 pb-3">
                <h3 class="text-base font-bold text-slate-900 flex items-center gap-2">
                    <i class="fa-solid fa-user-plus text-clinic-600"></i> Walk-in Patient Booking
                </h3>
                <button onclick="closeWalkinModal()" class="text-slate-400 hover:text-slate-600"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <form onsubmit="handleWalkinSubmit(event)" class="space-y-3">
                <input type="hidden" id="walkin-slot-id">
                <div>
                    <label class="text-xs font-semibold text-slate-600">Selected Slot</label>
                    <input type="text" id="walkin-slot-display" readonly class="w-full mt-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl text-sm font-semibold text-slate-700">
                </div>
                <div>
                    <label class="text-xs font-semibold text-slate-600">Patient Full Name *</label>
                    <input type="text" id="walkin-patient-name" required placeholder="e.g. Ramesh Kumar" class="w-full mt-1 px-3.5 py-2 border border-slate-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-clinic-500/20 focus:border-clinic-500">
                </div>
                <div>
                    <label class="text-xs font-semibold text-slate-600">WhatsApp Phone Number *</label>
                    <input type="tel" id="walkin-phone" required placeholder="e.g. 919876543210" class="w-full mt-1 px-3.5 py-2 border border-slate-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-clinic-500/20 focus:border-clinic-500">
                </div>
                <div>
                    <label class="text-xs font-semibold text-slate-600">Chief Complaint / Symptoms (Optional)</label>
                    <textarea id="walkin-notes" rows="2" placeholder="e.g. Viral fever for 3 days, cough" class="w-full mt-1 px-3.5 py-2 border border-slate-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-clinic-500/20 focus:border-clinic-500"></textarea>
                </div>
                <div class="flex items-center justify-end gap-2 pt-2">
                    <button type="button" onclick="closeWalkinModal()" class="px-4 py-2 rounded-xl text-sm font-semibold text-slate-600 hover:bg-slate-100">Cancel</button>
                    <button type="submit" class="px-4 py-2 rounded-xl text-sm font-semibold bg-clinic-600 hover:bg-clinic-700 text-white shadow-sm">Confirm Booking</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Modal: Broadcast Delay Notice -->
    <div id="broadcast-modal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 space-y-4 border border-slate-200">
            <div class="flex items-center justify-between border-b border-slate-100 pb-3">
                <h3 class="text-base font-bold text-slate-900 flex items-center gap-2">
                    <i class="fa-solid fa-bullhorn text-amber-500"></i> WhatsApp Delay Broadcast
                </h3>
                <button onclick="closeBroadcastModal()" class="text-slate-400 hover:text-slate-600"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <p class="text-xs text-slate-500">Send an instant WhatsApp delay announcement to all patients booked on the selected date.</p>

            <div class="space-y-2">
                <label class="text-xs font-semibold text-slate-700">Quick Templates:</label>
                <div class="grid grid-cols-2 gap-2">
                    <button onclick="setBroadcastPreset(30)" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-amber-50 text-amber-800 border border-amber-200 hover:bg-amber-100 text-left">
                        ⏱️ Running 30m Late
                    </button>
                    <button onclick="setBroadcastPreset(60)" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-amber-50 text-amber-800 border border-amber-200 hover:bg-amber-100 text-left">
                        ⏱️ Running 1h Late
                    </button>
                </div>
            </div>

            <form onsubmit="handleBroadcastSubmit(event)" class="space-y-3">
                <div>
                    <label class="text-xs font-semibold text-slate-600">Broadcast Message</label>
                    <textarea id="broadcast-message" rows="4" required class="w-full mt-1 px-3.5 py-2 border border-slate-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-amber-500/20 focus:border-amber-500"></textarea>
                </div>
                <div id="broadcast-result" class="text-xs font-semibold hidden"></div>
                <div class="flex items-center justify-end gap-2 pt-2">
                    <button type="button" onclick="closeBroadcastModal()" class="px-4 py-2 rounded-xl text-sm font-semibold text-slate-600 hover:bg-slate-100">Cancel</button>
                    <button type="submit" id="broadcast-btn" class="px-4 py-2 rounded-xl text-sm font-semibold bg-amber-500 hover:bg-amber-600 text-white shadow-sm flex items-center gap-2">
                        <i class="fa-brands fa-whatsapp"></i> Send Broadcast
                    </button>
                </div>
            </form>
        </div>
    </div>

    <!-- JavaScript Logic -->
    <script>
        function getCookie(name) {
            const value = `; ${document.cookie}`;
            const parts = value.split(`; ${name}=`);
            if (parts.length === 2) return parts.pop().split(';').shift();
            return null;
        }

        function checkAuth() {
            const pin = getCookie('dashboard_pin') || localStorage.getItem('dashboard_pin');
            if (!pin) {
                document.getElementById('login-modal').classList.remove('hidden');
                return false;
            }
            return true;
        }

        async function handleLogin(e) {
            e.preventDefault();
            const pin = document.getElementById('login-pin').value.trim();
            const errEl = document.getElementById('login-error');
            errEl.classList.add('hidden');

            try {
                const res = await fetch('/api/dashboard/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ pin: pin })
                });
                if (res.ok) {
                    localStorage.setItem('dashboard_pin', pin);
                    document.getElementById('login-modal').classList.add('hidden');
                    loadAppointments();
                } else {
                    errEl.innerText = 'Invalid PIN. Please try again.';
                    errEl.classList.remove('hidden');
                }
            } catch (err) {
                errEl.innerText = 'Network error connecting to server.';
                errEl.classList.remove('hidden');
            }
        }

        function logout() {
            document.cookie = 'dashboard_pin=; Max-Age=0; path=/;';
            localStorage.removeItem('dashboard_pin');
            document.getElementById('login-modal').classList.remove('hidden');
        }

        function setDateToday() {
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('date-picker').value = today;
            loadAppointments();
        }

        function setDateOffset(days) {
            const d = new Date();
            d.setDate(d.getDate() + days);
            document.getElementById('date-picker').value = d.toISOString().split('T')[0];
            loadAppointments();
        }

        async function loadAppointments() {
            if (!checkAuth()) return;

            const dateVal = document.getElementById('date-picker').value;
            const indicator = document.getElementById('loading-indicator');
            const tbody = document.getElementById('appointments-body');
            indicator.classList.remove('hidden');

            try {
                const pin = getCookie('dashboard_pin') || localStorage.getItem('dashboard_pin');
                const res = await fetch(`/api/dashboard/appointments?target_date=${dateVal}`, {
                    headers: { 'X-Dashboard-PIN': pin }
                });

                if (res.status === 401) {
                    logout();
                    return;
                }

                const data = await res.json();
                document.getElementById('schedule-title').innerText = `Schedule: ${data.date_display}`;
                document.getElementById('stat-total').innerText = data.summary.total;
                document.getElementById('stat-booked').innerText = data.summary.booked;
                document.getElementById('stat-completed').innerText = data.summary.completed;
                document.getElementById('stat-cancelled').innerText = data.summary.cancelled || 0;
                document.getElementById('stat-free').innerText = data.summary.free;

                tbody.innerHTML = '';
                if (data.appointments.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-slate-400">No slots or clinic hours scheduled for this date.</td></tr>`;
                    return;
                }

                data.appointments.forEach(appt => {
                    const tr = document.createElement('tr');
                    tr.className = 'hover:bg-slate-50/80 transition';

                    // Status Badge Styling
                    let badgeColor = 'bg-slate-100 text-slate-600 border-slate-200';
                    let badgeLabel = appt.status.toUpperCase();
                    if (appt.status === 'booked') {
                        badgeColor = 'bg-emerald-50 text-emerald-700 border-emerald-200';
                        badgeLabel = 'CONFIRMED';
                    } else if (appt.status === 'completed') {
                        badgeColor = 'bg-blue-50 text-blue-700 border-blue-200';
                        badgeLabel = 'COMPLETED';
                    } else if (appt.status === 'cancelled') {
                        badgeColor = 'bg-rose-50 text-rose-700 border-rose-200';
                        badgeLabel = 'CANCELLED';
                    } else if (appt.status === 'blocked') {
                        badgeColor = 'bg-slate-100 text-slate-700 border-slate-300';
                        badgeLabel = 'BLOCKED';
                    } else if (appt.status === 'free') {
                        badgeColor = 'bg-slate-50 text-slate-500 border-slate-200';
                        badgeLabel = 'AVAILABLE';
                    }

                    // WhatsApp link
                    const cleanPhone = appt.phone.replace(/\\D/g, '');
                    const waLink = cleanPhone ? `https://wa.me/${cleanPhone}` : '#';

                    // Actions
                    let actionsHtml = '';
                    if (appt.status === 'booked') {
                        actionsHtml = `
                            <div class="flex items-center justify-end gap-1.5">
                                <button onclick="updateStatus(${appt.id}, 'completed')" title="Mark Completed" class="px-2.5 py-1 text-xs font-semibold bg-blue-50 text-blue-700 hover:bg-blue-100 rounded-lg border border-blue-200 transition">
                                    <i class="fa-solid fa-check"></i> Done
                                </button>
                                <button onclick="updateStatus(${appt.id}, 'cancelled')" title="Cancel Appointment" class="px-2.5 py-1 text-xs font-semibold bg-rose-50 text-rose-700 hover:bg-rose-100 rounded-lg border border-rose-200 transition">
                                    <i class="fa-solid fa-xmark"></i> Cancel
                                </button>
                            </div>
                        `;
                    } else if (appt.status === 'cancelled') {
                        actionsHtml = `<span class="text-xs text-rose-500 font-semibold flex items-center justify-end gap-1"><i class="fa-solid fa-ban text-rose-500"></i> Cancelled</span>`;
                    } else if (appt.status === 'free') {
                        actionsHtml = `
                            <div class="flex items-center justify-end gap-1.5">
                                <button onclick="openWalkinModal(${appt.id}, '${appt.time}')" title="Book Walk-in" class="px-2.5 py-1 text-xs font-semibold bg-emerald-50 text-emerald-700 hover:bg-emerald-100 rounded-lg border border-emerald-200 transition">
                                    <i class="fa-solid fa-plus"></i> Book
                                </button>
                                <button onclick="updateStatus(${appt.id}, 'blocked')" title="Block Slot" class="px-2.5 py-1 text-xs font-semibold bg-slate-100 text-slate-600 hover:bg-slate-200 rounded-lg transition">
                                    <i class="fa-solid fa-ban"></i> Block
                                </button>
                            </div>
                        `;
                    } else if (appt.status === 'blocked') {
                        actionsHtml = `
                            <div class="flex items-center justify-end gap-1.5">
                                <button onclick="updateStatus(${appt.id}, 'free')" title="Unblock Slot" class="px-2.5 py-1 text-xs font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200 rounded-lg transition">
                                    <i class="fa-solid fa-lock-open"></i> Unblock
                                </button>
                            </div>
                        `;
                    } else if (appt.status === 'completed') {
                        actionsHtml = `<span class="text-xs text-slate-400 font-semibold flex items-center justify-end gap-1"><i class="fa-solid fa-circle-check text-emerald-500"></i> Done</span>`;
                    }

                    tr.innerHTML = `
                        <td class="px-6 py-4 font-bold text-slate-900 whitespace-nowrap">${appt.time}</td>
                        <td class="px-6 py-4 font-semibold text-slate-900">${appt.patient_name || '<span class="text-slate-300 italic">None</span>'}</td>
                        <td class="px-6 py-4">
                            ${appt.phone ? `
                                <a href="${waLink}" target="_blank" class="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-600 hover:text-emerald-700 hover:underline">
                                    <i class="fa-brands fa-whatsapp text-sm"></i> +${appt.phone}
                                </a>
                            ` : '<span class="text-slate-300">-</span>'}
                        </td>
                        <td class="px-6 py-4 whitespace-nowrap">
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold border ${badgeColor}">
                                ${badgeLabel}
                            </span>
                        </td>
                        <td class="px-6 py-4 text-xs text-slate-500 max-w-xs truncate">${appt.notes || '-'}</td>
                        <td class="px-6 py-4 text-right">${actionsHtml}</td>
                    `;
                    tbody.appendChild(tr);
                });

            } catch (err) {
                console.error(err);
            } finally {
                indicator.classList.add('hidden');
            }
        }

        async function updateStatus(id, newStatus) {
            const pin = getCookie('dashboard_pin') || localStorage.getItem('dashboard_pin');
            try {
                const res = await fetch('/api/dashboard/status', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Dashboard-PIN': pin
                    },
                    body: JSON.stringify({ appointment_id: id, status: newStatus })
                });
                if (res.ok) {
                    loadAppointments();
                } else {
                    alert('Could not update status');
                }
            } catch (err) {
                alert('Network error');
            }
        }

        // Walk-in Modal Handlers
        function openWalkinModal(slotId, slotTime) {
            document.getElementById('walkin-slot-id').value = slotId;
            document.getElementById('walkin-slot-display').value = `${document.getElementById('date-picker').value} at ${slotTime}`;
            document.getElementById('walkin-patient-name').value = '';
            document.getElementById('walkin-phone').value = '';
            document.getElementById('walkin-notes').value = '';
            document.getElementById('walkin-modal').classList.remove('hidden');
        }

        function closeWalkinModal() {
            document.getElementById('walkin-modal').classList.add('hidden');
        }

        async function handleWalkinSubmit(e) {
            e.preventDefault();
            const slotId = parseInt(document.getElementById('walkin-slot-id').value);
            const name = document.getElementById('walkin-patient-name').value.trim();
            const phone = document.getElementById('walkin-phone').value.trim();
            const notes = document.getElementById('walkin-notes').value.trim();
            const pin = getCookie('dashboard_pin') || localStorage.getItem('dashboard_pin');

            try {
                const res = await fetch('/api/dashboard/book', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Dashboard-PIN': pin
                    },
                    body: JSON.stringify({
                        appointment_id: slotId,
                        patient_name: name,
                        phone: phone,
                        notes: notes
                    })
                });
                if (res.ok) {
                    closeWalkinModal();
                    loadAppointments();
                } else {
                    alert('Error booking walk-in patient');
                }
            } catch (err) {
                alert('Network error');
            }
        }

        // Broadcast Modal Handlers
        function openBroadcastModal() {
            setBroadcastPreset(30);
            document.getElementById('broadcast-result').classList.add('hidden');
            document.getElementById('broadcast-modal').classList.remove('hidden');
        }

        function closeBroadcastModal() {
            document.getElementById('broadcast-modal').classList.add('hidden');
        }

        function setBroadcastPreset(mins) {
            const msg = `Notice from Dr. Rao's Clinic:\nDr. Rao is delayed by approximately ${mins} minutes due to an emergency medical consultation. We sincerely apologize for the delay and thank you for your understanding.`;
            document.getElementById('broadcast-message').value = msg;
        }

        async function handleBroadcastSubmit(e) {
            e.preventDefault();
            const dateVal = document.getElementById('date-picker').value;
            const message = document.getElementById('broadcast-message').value.trim();
            const pin = getCookie('dashboard_pin') || localStorage.getItem('dashboard_pin');
            const resultEl = document.getElementById('broadcast-result');
            const btn = document.getElementById('broadcast-btn');

            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending...';

            try {
                const res = await fetch('/api/dashboard/broadcast', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Dashboard-PIN': pin
                    },
                    body: JSON.stringify({ date: dateVal, message: message })
                });
                const data = await res.json();
                if (res.ok) {
                    resultEl.className = 'text-xs font-semibold text-emerald-600 mt-2';
                    resultEl.innerText = `Broadcast delivered to ${data.sent_count} of ${data.total_recipients} booked patients!`;
                    resultEl.classList.remove('hidden');
                    setTimeout(closeBroadcastModal, 2500);
                } else {
                    resultEl.className = 'text-xs font-semibold text-rose-600 mt-2';
                    resultEl.innerText = data.detail || 'Failed to send broadcast';
                    resultEl.classList.remove('hidden');
                }
            } catch (err) {
                resultEl.className = 'text-xs font-semibold text-rose-600 mt-2';
                resultEl.innerText = 'Network error during broadcast';
                resultEl.classList.remove('hidden');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-brands fa-whatsapp"></i> Send Broadcast';
            }
        }

        // Init on page load
        window.onload = () => {
            setDateToday();
            // Optional auto-refresh every 30 seconds
            setInterval(loadAppointments, 30000);
        };
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
