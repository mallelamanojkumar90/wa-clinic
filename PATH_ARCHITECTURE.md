# 🏥 Dr. Rao's Clinic - Multi-Channel Architecture Guide

This project is structured as a **Dual-Channel AI Receptionist** powered by a unified clinic operational core:

```
                              ┌────────────────────────────────────────┐
                              │           PATIENT / CALLER             │
                              └───────────────────┬────────────────────┘
                                                  │
                 ┌────────────────────────────────┴────────────────────────────────┐
                 │                                                                 │
                 ▼                                                                 ▼
      [ CHANNEL 1: WHATSAPP ]                                           [ CHANNEL 2: VOICE AI ]
     WhatsApp Chat & Voice Notes                                       Telephony Calls (Bolna/Vobiz)
                 │                                                                 │
                 ▼                                                                 ▼
    app/channels/whatsapp/                                             app/channels/voice/
    ├── router.py (Webhook /webhook)                                   ├── router.py (/api/voice/...)
    ├── agent.py (Multi-lingual LLM)                                   └── speech.py (TTS Formatters)
    ├── client.py (Meta Graph API)                                                 │
    ├── transcription.py (Whisper STT)                                             │
    └── security.py (HMAC Signature)                                               │
                 │                                                                 │
                 └────────────────────────────────┬────────────────────────────────┘
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │   SHARED CLINIC CORE  │
                                      │       (app/core/)     │
                                      └───────────┬───────────┘
                                                  │
         ┌────────────────────────┬───────────────┴───────────────┬────────────────────────┐
         ▼                        ▼                               ▼                        ▼
  [ Database & Models ]  [ Google Calendar ]              [ Booking Tools ]        [ Jev Triage & Pay ]
  • database.py          • calendar_service.py            • tools.py               • jev_service.py
  • models.py (Supabase) • Sync free/busy & events        • Slot engine & Buffer   • razorpay_service.py
```

---

## 📁 1. Project Directory Structure

```text
wa-clinic-poc/
├── app/
│   ├── channels/
│   │   ├── whatsapp/                    # 🟢 PATH 1: WHATSAPP RECEPTIONIST
│   │   │   ├── __init__.py              # Exports whatsapp_router & client helpers
│   │   │   ├── router.py                # Meta Webhooks: GET /webhook & POST /webhook
│   │   │   ├── agent.py                 # Multi-lingual LLM conversational agent
│   │   │   ├── client.py                # Meta Graph API sender (text, buttons, lists)
│   │   │   ├── transcription.py         # WhatsApp voice notes download & Whisper STT
│   │   │   └── security.py              # Meta HMAC-SHA256 signature verification
│   │   │
│   │   └── voice/                       # 🔵 PATH 2: TELEPHONY VOICE AI (Bolna/Vobiz)
│   │       ├── __init__.py              # Exports voice_router
│   │       ├── router.py                # Endpoints: /api/voice/slots, /book, /cancel
│   │       └── speech.py                # Natural spoken English formatting for TTS
│   │
│   ├── core/                            # ⚙️ SHARED CLINIC CORE & SERVICES
│   │   ├── __init__.py                  # Exports core services
│   │   ├── config.py                    # Clinic settings & environment variables
│   │   ├── database.py                  # Supabase PostgreSQL / SQLite sessionmaker
│   │   ├── models.py                    # SQLAlchemy Models (Appointment, Chat, Webhook)
│   │   ├── calendar_service.py          # Google Calendar live sync & event manager
│   │   ├── tools.py                     # Slot generation, 1-hr buffer, booking, cancelling
│   │   ├── jev_service.py               # TypeSafe Jev System-One triage & emergency check
│   │   ├── razorpay_service.py          # UPI advance token payments & hold release
│   │   └── scheduler.py                 # Automated 24h & 2h appointment reminder jobs
│   │
│   ├── dashboard/                       # 🖥️ CLINIC ADMIN WEB DASHBOARD
│   │   ├── __init__.py                  # Exports dashboard_router
│   │   └── router.py                    # Staff portal at /dashboard (queue, metrics, walk-ins)
│   │
│   ├── main.py                          # Unified FastAPI gateway mounting all routers
│   └── __init__.py
│
├── tests/                               # Automated test suites
├── scripts/                             # DB setup and simulation scripts
├── PATH_ARCHITECTURE.md                 # This documentation
└── render.yaml                          # Render cloud deployment specification
```

---

## 🟢 2. Path 1: WhatsApp AI Receptionist (`app/channels/whatsapp/`)

### Purpose
Handles patients messaging Dr. Rao's clinic over WhatsApp with text queries, button clicks, or audio voice notes in **English, Telugu, Hindi, Tenglish, or Hinglish**.

### Key Endpoints
| HTTP Method | URL Path | Description |
|:---|:---|:---|
| `GET` | `/webhook` *(or `/api/whatsapp/webhook`)* | Meta Developer verification challenge handshake (`hub.challenge`) |
| `POST` | `/webhook` *(or `/api/whatsapp/webhook`)* | Incoming messages, voice notes, button replies, and status updates |

### Files Breakdown
1. **[`router.py`](file:///c:/Users/malle/wa-clinic-poc/app/channels/whatsapp/router.py)**:
   - Receives incoming webhooks from Meta.
   - Verifies HMAC-SHA256 signature against `META_APP_SECRET`.
   - Prevents duplicate processing via `ProcessedWebhook` database table.
   - Offloads message processing to a background task so Meta receives an immediate `200 OK` within Meta's 5-second timeout.
2. **[`agent.py`](file:///c:/Users/malle/wa-clinic-poc/app/channels/whatsapp/agent.py)**:
   - Runs conversational LLM (`gpt-4o-mini` via OpenRouter).
   - Enforces multi-lingual instructions and conversational history.
   - Executes tool calling (`get_free_slots`, `book_slot`, `cancel_booking`).
3. **[`client.py`](file:///c:/Users/malle/wa-clinic-poc/app/channels/whatsapp/client.py)**:
   - Wraps Meta Graph API `POST /{phone_id}/messages`.
   - Sends plain text, interactive buttons, and slot list menus.
   - Marks messages as read (`mark_as_read` triggers blue checkmarks).
4. **[`transcription.py`](file:///c:/Users/malle/wa-clinic-poc/app/channels/whatsapp/transcription.py)**:
   - Downloads incoming `.ogg` voice notes from Meta's media CDN.
   - Transcribes audio using Groq Whisper (`whisper-large-v3-turbo`) or OpenAI Whisper.
5. **[`security.py`](file:///c:/Users/malle/wa-clinic-poc/app/channels/whatsapp/security.py)**:
   - Validates the `X-Hub-Signature-256` header.

---

## 🔵 3. Path 2: Telephony Voice AI Receptionist (`app/channels/voice/`)

### Purpose
Provides low-latency JSON REST endpoints for Voice AI phone agents (e.g. **Bolna.dev**, **Vobiz**, **Twilio**) so callers can check doctor availability, book appointments, and cancel bookings over a phone call.

### Key Endpoints
| HTTP Method | URL Path | Description |
|:---|:---|:---|
| `GET` | `/api/voice/slots` | Returns available clinic slots with natural spoken English phrasing |
| `POST` | `/api/voice/book` | Reserves slot, creates Google Calendar event, and sends WhatsApp confirmation |
| `POST` | `/api/voice/cancel` | Cancels appointment and removes event from Google Calendar |
| `POST` | `/api/voice/triage` | Fast (70-150ms) emergency and intent classification for inbound calls |

### Example Payloads

#### `GET /api/voice/slots?date=today`
```json
{
  "status": "success",
  "count": 2,
  "spoken_text": "Dr. Rao is available today at 6:00 PM. Which time works best for you?",
  "slots": ["Today at 6:00 PM"]
}
```

#### `POST /api/voice/book`
```json
{
  "patient_name": "Manoj Kumar",
  "phone": "+919030940864",
  "slot": "Today at 6:00 PM",
  "notes": "Mild fever and sore throat"
}
```
**Response:**
```json
{
  "status": "confirmed",
  "appointment_id": 137,
  "slot": "Thu 05 Nov at 06:00 PM",
  "google_event_id": "oomaqm98p1clkud712cj0ntq90",
  "spoken_text": "Your appointment with Dr. Rao is confirmed for Thu 5 Nov at 6:00 PM! I have sent the booking details directly to your WhatsApp."
}
```

---

## ⚙️ 4. Shared Clinic Core (`app/core/`)

Both channels share the exact same clinic state, ensuring zero double-bookings:
* **`tools.py`**: Enforces clinic operating hours (Mon-Sat 10am-1pm, 5pm-7pm), timezone (`Asia/Kolkata`), and a **1-hour minimum booking lead time buffer**.
* **`calendar_service.py`**: Bi-directionally syncs with Dr. Rao's Google Calendar.
* **`database.py` & `models.py`**: Connects to Supabase PostgreSQL with pooled connection management.
* **`jev_service.py`**: Fast medical emergency detection that immediately escalates emergencies to Emergency 108.
* **`razorpay_service.py`**: Generates 15-minute provisional slot holds and UPI payment links for advance tokens.
* **`scheduler.py`**: Background worker running automated 24-hour and 2-hour appointment reminders to patients on WhatsApp.
