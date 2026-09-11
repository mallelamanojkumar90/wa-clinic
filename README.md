# WhatsApp Clinic Receptionist (POC)

An intelligent AI-powered WhatsApp receptionist for a medical clinic (Dr. Rao's Clinic). The receptionist automatically handles patient appointment inquiries, shows available slots, books appointments, processes cancellations, and communicates naturally in the patient's language (English, Telugu, Hindi, etc.).

---

## Features

- 🕒 **Real-Time Slot Discovery (`get_free_slots`)**: Automatically lists available appointment slots and filters out past dates and already booked times.
- 📅 **Automated Booking (`book_slot`)**: Collects patient details (name, phone number) and books the requested slot in the database.
- ❌ **Instant Cancellation (`cancel_booking`)**: Allows patients to cancel their existing bookings directly over WhatsApp.
- 🌐 **Multi-Lingual Support**: Warmly responds in the patient's preferred language (English, Telugu, Hindi, etc.).
- 🔘 **Interactive & Button Support**: Seamlessly processes text messages, button clicks, and interactive replies.
- 💻 **Dual Run Modes**: Test locally in the terminal with zero Meta setup, or run as a live FastAPI webhook server connected to WhatsApp Cloud API.

---

## Project Structure

```text
wa-clinic/
├── app.py              # Core logic: SQLite DB, LLM tool definitions, chat loop, FastAPI webhook
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore rules (protects credentials and local DB)
└── README.md           # Documentation and setup guide
```

---

## Prerequisites

- **Python 3.10+**
- **OpenRouter API Key** (for accessing OpenAI, Gemini, or other LLMs)
- *(Optional for live WhatsApp mode)* **Meta for Developers Account** with WhatsApp Cloud API configured

---

## Quickstart

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/mallelamanojkumar90/wa-clinic.git
cd wa-clinic
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create your `.env` file from the template:

```bash
# On Linux/macOS
cp .env.example .env

# On Windows PowerShell
Copy-Item .env.example .env
```

Edit `.env` with your credentials:

```env
OPENROUTER_API_KEY=your_openrouter_api_key
MODEL=openai/gpt-4o-mini

# WhatsApp Cloud API (required only for webhook mode)
WHATSAPP_TOKEN=your_meta_access_token
WHATSAPP_PHONE_ID=your_whatsapp_phone_number_id
VERIFY_TOKEN=your_custom_webhook_secret_token
```

---

## Running the Application

### Mode A: Terminal Chat Demo (No WhatsApp Needed)

Test the conversation flow, multi-language replies, and tool calling directly in your console:

```bash
python app.py chat
```

Example interaction:
```text
Clinic receptionist POC. 'quit' to exit.
you> Hi, do you have any free appointments tomorrow?
bot> Hello! Yes, we have the following slots available tomorrow (Saturday):
- 10:00 AM
- 11:00 AM
- 12:00 PM
- 05:00 PM
- 06:00 PM
Would you like me to book one for you? Please provide your name.
```

### Mode B: Live WhatsApp Webhook Server

Start the FastAPI webhook server:

```bash
uvicorn app:app --port 8000
```

The server exposes:
- `GET /` — Health check endpoint.
- `GET /webhook` — Meta challenge verification handshake.
- `POST /webhook` — Incoming WhatsApp message receiver and AI responder.

---

## WhatsApp Cloud API Integration (Meta)

### 1. Get Test Credentials
1. Go to [Meta for Developers](https://developers.facebook.com/) and create or open your **Business App**.
2. Under **WhatsApp > API Setup**:
   - Copy the **Temporary access token** into `WHATSAPP_TOKEN` in `.env`.
   - Copy the **Phone number ID** into `WHATSAPP_PHONE_ID` in `.env`.
   - Under **Manage phone number list**, add and verify your recipient phone number with an OTP.

### 2. Verify Your Credentials
You can verify your WhatsApp token via curl:

```bash
curl -i -X GET "https://graph.facebook.com/v21.0/<WHATSAPP_PHONE_ID>" \
  -H "Authorization: Bearer <WHATSAPP_TOKEN>"
```

### 3. Expose Webhook to the Internet
Meta requires an HTTPS endpoint. You can use Cloudflare Tunnel or ngrok:

```bash
# Using Cloudflare Tunnel
cloudflared tunnel --url http://localhost:8000

# OR using ngrok
ngrok http 8000
```

### 4. Configure Webhook in Meta Dashboard
1. In Meta Developer Dashboard, navigate to **WhatsApp > Configuration**.
2. Click **Edit** under **Webhook**:
   - **Callback URL**: `https://<your-public-url>/webhook`
   - **Verify token**: Value matching `VERIFY_TOKEN` in your `.env`.
3. Click **Verify and save**.
4. In Webhook fields, click **Manage** and subscribe to **`messages`**.

Now send a WhatsApp message to your test number from your verified phone!

---

## Transitioning from POC to Production

To take this Proof-of-Concept into a full production medical clinic environment:

1. **Dedicated Cloud Hosting**: Deploy the FastAPI service to a cloud provider (e.g., Render, Railway, AWS, Fly.io) with a permanent custom domain and SSL.
2. **Official WhatsApp Business Number**: Register the clinic's real phone number and generate a non-expiring **System User Access Token** in Meta Business Manager.
3. **Robust Database**: Migrate from SQLite (`clinic.db`) to **PostgreSQL** (e.g., Supabase, Neon, AWS RDS) to safely manage concurrent patient bookings.
4. **Persistent Conversation Store**: Store conversation state (`_histories`) in **Redis** or a database table so context persists across server restarts and multiple instances.
5. **Webhook Signature Verification**: Validate Meta's `X-Hub-Signature-256` header using the Meta App Secret to ensure requests originate exclusively from Meta.
6. **Calendar / Practice Management Integration**: Replace static slot queries with integrations to **Google Calendar**, **Outlook**, or practice software (e.g., Practo, AthenaHealth, Epic).
