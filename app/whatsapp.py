"""WhatsApp client re-export for backwards compatibility. Implementation moved to app.channels.whatsapp.client."""
from app.channels.whatsapp.client import (
    send_message,
    mark_as_read,
    send_interactive_buttons,
    send_interactive_list,
    send_slots_interactive_menu,
    send_welcome_action_buttons,
    GRAPH_API_VERSION,
    _headers,
)

__all__ = [
    "send_message",
    "mark_as_read",
    "send_interactive_buttons",
    "send_interactive_list",
    "send_slots_interactive_menu",
    "send_welcome_action_buttons",
    "GRAPH_API_VERSION",
    "_headers",
]
