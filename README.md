# WhatsApp Clinic Receptionist (POC)

AI receptionist that answers a clinic's WhatsApp: shows free slots, books/cancels appointments, replies in the patient's language.

## Run

```powershell
pip install openai fastapi requests
# fill in .env, then:
py -3.12 app.py chat          # terminal demo, no WhatsApp needed
uvicorn app:app --port 8000   # webhook server for real WhatsApp
```

## Setup

- `.env` — put your keys there (see .env.example / .env)
- Model via `MODEL` env var (any OpenRouter model id; default `openai/gpt-4o-mini`)
- WhatsApp Cloud API: set Meta webhook URL to `https://<your-host>/webhook`, verify token = `VERIFY_TOKEN`

## Files

- `app.py` — everything: SQLite schema, LLM agent + tools (`get_free_slots`, `book_slot`, `cancel_booking`), terminal chat, FastAPI webhook

## Notes

- Slots are seeded 2 days ahead (10–12, 17–18h); delete `clinic.db` to reset
