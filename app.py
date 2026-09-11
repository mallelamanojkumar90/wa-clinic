"""POC: WhatsApp clinic receptionist.

Run modes:
  python app.py chat        -- terminal chat, no Meta account needed
  uvicorn app:app --port 8000   -- webhook server for WhatsApp Cloud API

Env: OPENROUTER_API_KEY, MODEL (default openai/gpt-4o-mini),
     WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, VERIFY_TOKEN (only for real WhatsApp sending).
"""
import json, os, sqlite3, sys
from datetime import datetime, timedelta
from dotenv import load_dotenv; load_dotenv()

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

DB = os.path.join(os.path.dirname(__file__), "clinic.db")
MODEL = os.environ.get("MODEL", "openai/gpt-4o-mini")  # any OpenRouter model id

# ---------- DB ----------
def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS appointments(
        id INTEGER PRIMARY KEY,
        patient_name TEXT, phone TEXT,
        slot TEXT UNIQUE,           -- ISO datetime
        status TEXT DEFAULT 'booked')""")
    return con

def seed_slots():
    con = db()
    now_iso = datetime.now().isoformat()
    # Clean up past unbooked slots so they never clutter availability
    con.execute("DELETE FROM appointments WHERE status='free' AND slot < ?", (now_iso,))
    base = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
    for day in range(2):
        for hour in (10, 11, 12, 17, 18):
            slot = (base + timedelta(days=day)).replace(hour=hour)
            con.execute("INSERT OR IGNORE INTO appointments(patient_name,phone,slot,status) VALUES('', '', ?, 'free')",
                        (slot.isoformat(),))
    con.commit(); con.close()

# ---------- tools the LLM can call ----------
def get_free_slots():
    now_iso = datetime.now().isoformat()
    rows = db().execute(
        "SELECT slot FROM appointments WHERE status='free' AND slot > ? ORDER BY slot",
        (now_iso,),
    ).fetchall()
    out = []
    for (s,) in rows:
        d = datetime.fromisoformat(s)
        out.append(f'{s} ({d.strftime("%a %d %b %I:%M %p")})')
    return out

def book_slot(slot: str, name: str, phone: str):
    # naive substring match on date+time; switch to parsed datetimes if ambiguity bites
    now_iso = datetime.now().isoformat()
    rows = db().execute(
        "SELECT slot FROM appointments WHERE status='free' AND slot > ?",
        (now_iso,),
    ).fetchall()
    want = slot.replace(",", "").replace("  ", " ").lower().strip()
    match = None
    for (s,) in rows:
        d = datetime.fromisoformat(s)
        pretty = d.strftime("%a %d %b %I:%M %p").replace(", ", " ").lower()
        if want in s.lower() or want in pretty or pretty in want:
            match = s
            break
    if not match:
        return f"Slot '{slot}' not found. Available: {[datetime.fromisoformat(s).strftime('%a %d %b %I:%M %p') for (s,) in rows]}"
    con = db()
    con.execute("UPDATE appointments SET patient_name=?, phone=?, status='booked' WHERE slot=?", (name, phone.lstrip("+"), match))
    con.commit()
    return f"Booked {name} at {match}"

def cancel_booking(phone: str):
    con = db()
    cur = con.execute("UPDATE appointments SET patient_name='', phone='', status='free' WHERE phone=? AND status='booked'", (phone,))
    con.commit()
    return "Cancelled." if cur.rowcount else "No booking found for this number."

TOOLS = [
    {"type": "function", "function": {"name": "get_free_slots", "description": "List free appointment slots",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "book_slot", "description": "Book an appointment",
     "parameters": {"type": "object", "properties": {
         "slot": {"type": "string"}, "name": {"type": "string"}, "phone": {"type": "string"}},
         "required": ["slot", "name", "phone"]}}},
    {"type": "function", "function": {"name": "cancel_booking", "description": "Cancel caller's appointment by their phone number",
     "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]}}},
]

SYSTEM = """You are the WhatsApp receptionist of Dr. Rao's clinic (Mon-Sat).
Reply briefly and warmly; match the patient's language (English/Telugu/Hindi).
Help them see slots, book (get name + confirm slot), or cancel.
ALWAYS call get_free_slots before telling the patient anything about availability.
Slot strings must be copied verbatim from get_free_slots results when booking."""

# ---------- LLM loop (OpenAI-compatible; works for Gemini via OpenRouter-style base) ----------
from openai import OpenAI
def _client():
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])

def reply(user_text: str, phone: str, history: list) -> str:
    history.append({"role": "user", "content": user_text})
    msgs = [{"role": "system", "content": SYSTEM + f" Patient phone: {phone}"}] + history[-10:]
    target_model = MODEL.split("/", 1)[1] if "/" in MODEL else MODEL
    for _ in range(4):  # ponytail: max 4 tool rounds per message
        r = _client().chat.completions.create(model=target_model, messages=msgs, tools=TOOLS)
        m = r.choices[0].message
        msgs.append(m)
        if not m.tool_calls:
            history.append({"role": "assistant", "content": m.content})
            return m.content
        for tc in m.tool_calls:
            args = json.loads(tc.function.arguments)
            print(f"[tool] {tc.function.name}({args})")  # debug: shows what the model does
            fn = {"get_free_slots": lambda a: get_free_slots(),
                  "book_slot": lambda a: book_slot(**a),
                  "cancel_booking": lambda a: cancel_booking(a["phone"].lstrip("+"))}[tc.function.name]
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": str(fn(args))})
    return "Sorry, please try again."

# ---------- terminal chat ----------
def chat():
    seed_slots()
    hist, phone = [], "+919876543210"
    print("Clinic receptionist POC. 'quit' to exit.")
    while (t := input("you> ").strip()) not in ("quit", ""):
        print("bot>", reply(t, phone, hist))

# ---------- webhook ----------
def send_whatsapp(to, text):
    import requests
    res = requests.post(
        f"https://graph.facebook.com/v21.0/{os.environ['WHATSAPP_PHONE_ID']}/messages",
        headers={"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}"},
        json={"messaging_product": "whatsapp", "to": to, "text": {"body": text}},
        timeout=10,
    )
    print(f"[send_whatsapp] to={to} status={res.status_code} res={res.text}")

_histories = {}  # phone -> messages  (in-memory, move to Postgres when >1 process)

def create_app():
    from fastapi import FastAPI, Request, Response, Query
    seed_slots()
    app = FastAPI(title="WhatsApp Clinic Receptionist")

    @app.get("/")
    def health():
        return {"status": "ok", "service": "WhatsApp Clinic Receptionist"}

    @app.get("/webhook")  # Meta verification handshake
    def verify(
        hub_mode: str = Query("", alias="hub.mode"),
        hub_verify_token: str = Query("", alias="hub.verify_token"),
        hub_challenge: str = Query("", alias="hub.challenge"),
    ):
        print(f"[webhook-verify] mode={hub_mode} token={hub_verify_token} challenge={hub_challenge}")
        if hub_mode == "subscribe" and hub_verify_token == os.environ.get("VERIFY_TOKEN"):
            return Response(content=hub_challenge, media_type="text/plain")
        return Response(content="Verification token mismatch", status_code=403)

    @app.post("/webhook")
    async def webhook(req: Request):
        body = await req.json()
        print(f"[webhook] received: {json.dumps(body)}")
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    phone = msg.get("from")
                    if not phone:
                        continue
                    text = ""
                    if msg.get("type") == "text" and "text" in msg:
                        text = msg["text"].get("body", "")
                    elif "button" in msg:
                        text = msg["button"].get("text", "")
                    elif "interactive" in msg:
                        text = msg["interactive"].get("button_reply", {}).get("title", "")

                    if text:
                        print(f"[webhook] processing message from +{phone}: '{text}'")
                        out = reply(text, "+" + phone, _histories.setdefault(phone, []))
                        try:
                            send_whatsapp(phone, out)
                        except Exception as e:
                            print("[webhook] send failed:", e)
        return {"ok": True}

    return app

app = create_app()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        chat()
