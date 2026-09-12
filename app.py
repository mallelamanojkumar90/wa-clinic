"""WhatsApp Clinic Receptionist - Root Entrypoint.

Usage:
  Terminal Chat Mode: python app.py chat
  Production Webhook: uvicorn app:app --port 8000
"""
import sys
from app.main import app
from app.agent import reply
from app.tools import seed_slots_if_needed

def chat():
    """Terminal chat demo for testing conversation, language fluency, and tool calling."""
    seed_slots_if_needed()
    phone = "+919030940864"
    print("=" * 60)
    print("Dr. Rao's Clinic - WhatsApp AI Receptionist (Terminal Demo)")
    print("Languages: English / Telugu / Hindi / Tenglish / Hinglish")
    print("Type 'quit' to exit.")
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou > ").strip()
            if not user_input or user_input.lower() in ("quit", "exit"):
                print("Goodbye!")
                break

            response = reply(user_input, phone)
            print(f"\nReceptionist > {response}")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        chat()
    else:
        import uvicorn
        from app.config import settings
        uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=settings.DEBUG)
