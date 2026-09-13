# WhatsApp Clinic Receptionist (Production Ready)

An intelligent, multi-lingual, AI-powered WhatsApp receptionist for medical clinics (Dr. Rao's Clinic). 

Built with **FastAPI**, **Supabase (PostgreSQL)**, **Google Calendar API**, **OpenRouter LLMs** (GPT-4o-mini / Gemini 2.0 Flash), and deployed seamlessly on **Render**.

---

## 🌟 Key Features

- 🌐 **Native Multi-Lingual Intelligence**:
  - Automatically speaks the patient's language: **Telugu**, **Hindi**, **English**, **Tenglish** (*"Repu 11 AM ki appointment kavali"*), **Hinglish** (*"Kal doctor available hai kya?"*), etc.
- 🎙️ **Voice Notes & Audio Transcription (Whisper)**:
  - Patients can send native WhatsApp voice notes in Telugu, Hindi, or English.
  - Automatically downloaded from Meta and transcribed with **Groq Whisper** (`whisper-large-v3-turbo`) or **OpenAI Whisper** (`whisper-1`).
- 🔘 **Interactive Buttons & Tap-to-Book Slot Lists**:
  - Interactive reply buttons (`[ 📅 View Slots ]`, `[ ❌ Cancel Booking ]`, `[ ℹ️ Clinic Timings ]`).
  - Native WhatsApp List Pickers display upcoming slots for one-tap booking without manual typing.
- ⏰ **Automated Appointment Reminders (24h & 2h Alerts)**:
  - Background scheduler (`APScheduler`) sends proactive WhatsApp reminders at **T-24h** and **T-2h**.
  - Includes cancellation/reschedule instructions to minimize clinic no-shows.
- 💻 **Front-Desk Live Web Dashboard (`/dashboard`)**:
  - Secure web interface for Dr. Rao & front-desk staff (PIN-protected).
  - Live patient queue, 1-click consultation completion, slot blocking/unblocking, and walk-in bookings.
  - **Emergency Delay Broadcast**: 1-click WhatsApp announcement to today's booked patients (*"Doctor running 30m late"*).
- 📅 **Live Google Calendar Real-Time Sync**:
  - Automatically queries Dr. Rao's actual clinic calendar free/busy blocks so double-bookings are impossible.
  - Automatically books calendar events with patient name, phone, and reminder alerts.
  - Deletes/cancels calendar events when an appointment is cancelled.
- 🗄️ **Supabase (PostgreSQL) Persistent Memory**:
  - Stores all appointments and conversation history in Supabase.
  - Patients can leave WhatsApp and return days later; their conversation context is never lost.
- 🔒 **Production Security & Idempotency**:
  - Meta `X-Hub-Signature-256` HMAC-SHA256 signature validation ensures requests come exclusively from Meta.
  - Deduplication engine prevents duplicate AI replies when Meta retries webhooks.
  - Sends immediate "read receipts" (blue check marks) and typing indicators.
- 🚀 **One-Click Cloud Deployment**:
  - Preconfigured for **Render** (`render.yaml` and `Dockerfile`).

---

## 📁 Architecture

```text
wa-clinic/
├── app/
│   ├── config.py           # Pydantic Settings (Supabase, Google Calendar, Meta, OpenRouter, Whisper)
│   ├── database.py         # SQLAlchemy engine with Supabase Postgres pool (SQLite dev fallback)
│   ├── models.py           # Models: Appointment, ChatMessage, ProcessedWebhook (with reminder flags)
│   ├── security.py         # HMAC-SHA256 Meta webhook signature verification
│   ├── calendar_service.py # Google Calendar API: freebusy queries, event creation & cancellation
│   ├── tools.py            # AI tools: get_free_slots, book_slot, cancel_booking
│   ├── agent.py            # Multi-lingual conversational agent loop with tool dispatcher
│   ├── whatsapp.py         # Meta Cloud API: message sending, interactive buttons, list pickers
│   ├── transcription.py    # Meta media download & Groq/OpenAI Whisper transcription
│   ├── scheduler.py        # APScheduler automated 24h & 2h appointment reminder worker
│   ├── dashboard.py        # Front-desk live web dashboard router & UI (/dashboard)
│   └── main.py             # FastAPI entrypoint, health checks, webhook handlers
├── scripts/
│   └── setup_supabase.sql  # Production Supabase SQL migration script
├── tests/
│   └── test_production.py  # Automated test suite
├── Dockerfile              # Production multi-stage container
├── render.yaml             # Render one-click blueprint
├── requirements.txt        # Production Python dependencies
├── .env.example            # Production environment variable template
└── app.py                  # CLI chat runner and server entrypoint
```

---

## 🚀 Production Deployment Guide

### Step 1: Set up Supabase (Database)
1. Log in to [Supabase](https://supabase.com) and create a new project (e.g. `dr-rao-clinic`).
2. Go to the **SQL Editor** in the left sidebar.
3. Open [`scripts/setup_supabase.sql`](file:///c:/Users/malle/wa-clinic-poc/scripts/setup_supabase.sql), copy the entire SQL script, paste it into the Supabase SQL Editor, and click **Run**.
4. Go to **Project Settings** ➔ **Database** ➔ **Connection string**:
   - Select **URI** (or Transaction Pooler mode).
   - Copy the URI (e.g., `postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres`).
   - This is your `DATABASE_URL`.

---

### Step 2: Set up Google Calendar API (Live Clinic Sync)
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project and enable the **Google Calendar API**.
3. Navigate to **IAM & Admin** ➔ **Service Accounts** ➔ **Create Service Account**.
4. Once created, click on the Service Account ➔ **Keys** tab ➔ **Add Key** ➔ **Create new key (JSON)**. Download the JSON file.
5. Copy the Service Account email address (e.g., `clinic-bot@your-project.iam.gserviceaccount.com`).
6. Open **Google Calendar** (for the clinic / Dr. Rao) ➔ Go to the Calendar settings ➔ Under **Share with specific people**, add the Service Account email and set permission to **"Make changes to events"**.
7. Set `GOOGLE_SERVICE_ACCOUNT_JSON` to the JSON key content (or file path), and `GOOGLE_CALENDAR_ID` to the clinic calendar ID (usually the doctor's email, or `primary`).

*(Note: If you don't configure Google Calendar immediately, the bot automatically falls back to managing slots directly in Supabase).*

---

### Step 3: Deploy to Render
1. Push this repository to your GitHub account:
   ```bash
   git add .
   git commit -m "Production release: Render, Supabase, Google Calendar, Multi-lingual"
   git push origin main
   ```
2. Log in to [Render](https://render.com) and click **New +** ➔ **Blueprint**.
3. Connect your GitHub repository. Render will automatically detect [`render.yaml`](file:///c:/Users/malle/wa-clinic-poc/render.yaml).
4. Fill in the environment variables when prompted:
   - `OPENROUTER_API_KEY`: Your OpenRouter API Key.
   - `MODEL`: `openai/gpt-4o-mini` (or `google/gemini-2.0-flash`).
   - `GROQ_API_KEY`: Your Groq API Key for Whisper voice transcription (optional, for voice notes).
   - `WHATSAPP_TOKEN`: Permanent System User token (from Meta Business Manager).
   - `WHATSAPP_PHONE_ID`: Your WhatsApp Phone Number ID.
   - `VERIFY_TOKEN`: A secret token of your choice (e.g. `manojkumar`).
   - `META_APP_SECRET`: Your Meta App Secret (found in App Dashboard ➔ App Settings ➔ Basic).
   - `DATABASE_URL`: Your Supabase PostgreSQL connection string.
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: Your Google Cloud Service Account JSON string.
   - `GOOGLE_CALENDAR_ID`: The clinic calendar ID or `primary`.
5. Click **Apply**. Render will build and deploy your service, giving you a live permanent HTTPS domain:
   `https://wa-clinic-receptionist.onrender.com`

---

### Step 4: Configure Meta WhatsApp Webhook
1. Open the [Meta Developer Dashboard](https://developers.facebook.com/apps/) ➔ Select your App.
2. Go to **WhatsApp** ➔ **Configuration** (in the left menu).
3. Under **Webhook**, click **Edit**:
   - **Callback URL**: `https://<your-render-app>.onrender.com/webhook`
   - **Verify token**: The value of your `VERIFY_TOKEN` (e.g. `manojkumar`).
4. Click **Verify and save**.
5. Under **Webhook fields**, click **Manage** and subscribe to **`messages`**.

---

## 🔑 How to Generate a Permanent Meta System User Token (Never Expires)

By default, Meta's Developer Dashboard gives you a **temporary token that expires in 24 hours**. When this token expires, your bot will stop replying to patients.

To make your token **permanent (never-expiring)** for production:

1. Open [Meta Business Settings](https://business.facebook.com/settings/).
2. In the left navigation, go to **Users** ➔ **System users**.
3. Click **Add**:
   - **System username**: `clinic-bot-user`
   - **System user role**: `Admin`
   - Click **Create system user**.
4. Click **Assign assets**:
   - Select **WhatsApp Accounts** (or Apps).
   - Select your clinic app / WhatsApp account.
   - Toggle on **Full control** (Manage WhatsApp account).
   - Click **Save changes**.
5. Click **Generate new token**:
   - Select your App from the dropdown.
   - Under **Token expiration**, select **Never** (or the maximum allowable).
   - Under **Available permissions**, check:
     - `whatsapp_business_messaging`
     - `whatsapp_business_management`
   - Click **Generate token**.
6. **Copy and save this token immediately** (Meta will only show it once).
7. Paste this permanent token into Render:
   - Go to [dashboard.render.com](https://dashboard.render.com/) ➔ `wa-clinic-receptionist` ➔ **Environment**.
   - Edit `WHATSAPP_TOKEN` with this new permanent token and click **Save Changes**.

---

## 🛠️ Troubleshooting & FAQ

### 1. "The bot received my message, but I didn't get a WhatsApp reply"
- **Cause**: The `WHATSAPP_TOKEN` has expired (Meta returns `401 Unauthorized`).
- **Fix**: Refresh your temporary token in Meta Dashboard (or generate a permanent System User token as described above), then update `WHATSAPP_TOKEN` in Render under **Environment** ➔ **Save Changes**.

### 2. "Why am I receiving Jasper's Market grocery store messages?"
- **Cause**: You clicked the blue **"Send message"** button in Meta Developer Console under API Setup. That button is Meta's hardcoded demo template sender.
- **Fix**: Do not click that button. Simply open the **WhatsApp app on your mobile phone** and send a real message to the bot's phone number (`+1 555-168-0916`).

### 3. "How do I verify the server is live and healthy?"
Visit your Render root URL in your browser:
`https://<your-app>.onrender.com/`
It should return `200 OK` with:
```json
{
  "status": "online",
  "service": "Dr. Rao Clinic WhatsApp AI Receptionist",
  "version": "2.0.0",
  "database": "postgresql (supabase)",
  "google_calendar_sync": true,
  "model": "openai/gpt-4o-mini"
}
```

---

## 💻 Local Testing & CLI Chat Mode

You can test the receptionist logic, multi-lingual fluency, and tool calls locally without any WhatsApp setup:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run terminal chat
python app.py chat
```

### Example Multi-Lingual Interactions

#### In Telugu / Tenglish:
```text
You > నమస్కారం, రేపు ఉదయం డాక్టర్ గారి అపాయింట్‌మెంట్ దొరుకుతుందా?
Receptionist > నమస్కారం! అవును, రేపు (శనివారం) ఉదయం కింది స్లాట్లు అందుబాటులో ఉన్నాయి:
- 10:00 AM
- 11:00 AM
- 12:00 PM
మీరు ఏ సమయాన్ని బుక్ చేయాలనుకుంటున్నారు? దయచేసి మీ పూర్తి పేరు తెలియజేయండి.
```

#### In Hindi / Hinglish:
```text
You > Kal subah ka appointment mil sakta hai kya? Name Manoj
Receptionist > Namaste Manoj ji! Haan, kal subah ye slots available hain:
- 10:00 AM
- 11:00 AM
- 12:00 PM
Aap kaunsa time book karna chahenge?
```

#### In English:
```text
You > Hi, I'd like to book Saturday at 11:00 AM for Manoj.
Receptionist > Hello Manoj! Your appointment for Saturday, 12 Sep at 11:00 AM has been successfully booked.
```

