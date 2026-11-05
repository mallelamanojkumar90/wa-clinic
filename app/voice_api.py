"""Voice AI API re-export for backwards compatibility. Implementation moved to app.channels.voice."""
from app.channels.voice.router import (
    router,
    VoiceBookingRequest,
    VoiceCancelRequest,
    VoiceTriageRequest,
    get_voice_slots,
    book_voice_slot,
    cancel_voice_booking,
    voice_triage,
)
from app.channels.voice.speech import (
    _format_slots_for_speech,
    _clean_human_slot_labels,
)

__all__ = [
    "router",
    "VoiceBookingRequest",
    "VoiceCancelRequest",
    "VoiceTriageRequest",
    "get_voice_slots",
    "book_voice_slot",
    "cancel_voice_booking",
    "voice_triage",
    "_format_slots_for_speech",
    "_clean_human_slot_labels",
]
