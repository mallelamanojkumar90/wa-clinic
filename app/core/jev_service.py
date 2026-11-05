"""TypeSafe Jev System-One Decision Engine for Clinic Triage and Fast-Routing.

Leverages TypeSafe AI's Jev model (System-1 fast non-autoregressive decision model)
for low-latency (70-150ms) structured classification:
- Medical Emergency Triage (Noul)
- Intent Classification & Fast Routing (Choice)
- Urgency Scoring & Front-Desk Escalation (Score)
"""
import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

class TriageResult(BaseModel):
    is_emergency: bool = Field(False, description="Whether the message indicates an acute medical emergency")
    intent: str = Field("general_inquiry", description="Categorized user intent")
    urgency: str = Field("low", description="Urgency level: low, medium, urgent")
    confidence: Optional[float] = Field(None, description="Confidence score from Jev")
    source: str = Field("heuristic", description="'jev' if classified by Jev API, 'heuristic' if fallback")
    raw_answers: Dict[str, Any] = Field(default_factory=dict, description="Raw details from Jev response")

    @property
    def should_bypass_llm(self) -> bool:
        """Determines if the request can be resolved immediately without calling the generative LLM."""
        return self.is_emergency or self.intent in ("emergency", "clinic_info", "check_slots")

class JevService:
    """Service wrapper for TypeSafe Jev System-One decision model."""

    def __init__(self):
        self._client = None
        self._init_client()

    def _init_client(self):
        if settings.TYPESAFE_API_KEY:
            try:
                from typesafe_sdk import TypeSafeClient
                self._client = TypeSafeClient(api_key=settings.TYPESAFE_API_KEY)
                logger.info("TypeSafe Jev client initialized successfully.")
            except ImportError:
                logger.warning("typesafe-sdk is not installed. Falling back to heuristic triage.")
            except Exception as e:
                logger.warning(f"Could not initialize TypeSafeClient: {e}")

    @property
    def is_available(self) -> bool:
        """Check if Jev API is active and configured with an API key."""
        return bool(settings.ENABLE_JEV and self._client is not None and settings.TYPESAFE_API_KEY)

    def triage_patient_message(self, text: str) -> TriageResult:
        """Run System-One decision analysis on an incoming patient query or transcription."""
        clean_text = (text or "").strip()
        if not clean_text:
            return TriageResult(source="empty")

        # 1. Try Jev System-One Model if enabled and configured
        if self.is_available:
            try:
                return self._call_jev_system_one(clean_text)
            except Exception as e:
                logger.error(f"Jev API call failed, falling back to heuristic triage: {e}")

        # 2. Resilient Rule-Based Heuristic Fallback
        return self._heuristic_triage(clean_text)

    def _call_jev_system_one(self, text: str) -> TriageResult:
        """Execute Jev System-One API request with structured Choice, Noul, and Score questions."""
        from typesafe_sdk import Choice, Noul, Score

        questions = {
            "is_emergency": Noul(
                instructions=(
                    "Determine if the user's message indicates an acute, life-threatening medical emergency "
                    "requiring immediate ambulance or emergency room care (e.g., severe chest pain, heart attack, "
                    "inability to breathe/gasping, heavy or unstoppable bleeding, stroke symptoms like slurred speech or facial drooping, "
                    "unconsciousness/fainting, severe head injury, anaphylaxis). "
                    "Routine appointments, cold/fever, general inquiries, or cancellations are NOT medical emergencies."
                )
            ),
            "intent": Choice(
                instructions=(
                    "Classify the primary intent of the patient or caller. Understand English, Telugu, Hindi, Tenglish, or Hinglish."
                ),
                criteria={
                    "emergency": "Acute medical emergency requiring immediate hospitalization or ambulance",
                    "check_slots": "Asking for available timings, free slots, doctor schedule, or clinic availability",
                    "book_appointment": "Requesting to book, schedule, or reserve an appointment for a patient",
                    "cancel_booking": "Requesting to cancel or reschedule an existing booking",
                    "clinic_info": "Asking about clinic hours, address, location, Dr. Rao's fees, or clinic directions",
                    "general_inquiry": "Asking general medical advice, symptoms, questions, greetings, or other conversation"
                }
            ),
            "urgency": Score(
                instructions="Assess the urgency of this patient query.",
                criteria=["low", "medium", "urgent"]
            )
        }

        response = self._client.system_one(state=text, questions=questions)
        answers = response.answers if hasattr(response, "answers") else {}

        # Parse Noul (Probability float 0.0 - 1.0)
        is_emerg_ans = answers.get("is_emergency")
        noul_prob = float(getattr(is_emerg_ans, "noul", 0.0) or 0.0)
        is_emergency = noul_prob >= 0.65

        # Parse Choice
        intent_ans = answers.get("intent")
        intent = str(getattr(intent_ans, "choice", "general_inquiry"))
        confidence = getattr(intent_ans, "confidence", None)

        # Parse Score (Float weighted average 0.0 - 2.0: 0=low, 1=medium, 2=urgent)
        urgency_ans = answers.get("urgency")
        raw_score = float(getattr(urgency_ans, "score", 0.0) or 0.0)
        if raw_score >= 1.4:
            urgency = "urgent"
        elif raw_score >= 0.6:
            urgency = "medium"
        else:
            urgency = "low"

        # Reconcile emergency flag
        if is_emergency or intent == "emergency":
            is_emergency = True
            intent = "emergency"
            urgency = "urgent"

        return TriageResult(
            is_emergency=is_emergency,
            intent=intent,
            urgency=urgency,
            confidence=confidence,
            source="jev",
            raw_answers={
                k: {"type": getattr(v, "type", str(type(v))), "value": getattr(v, getattr(v, "type", ""), None)}
                for k, v in answers.items()
            }
        )

    def _heuristic_triage(self, text: str) -> TriageResult:
        """Deterministic multilingual heuristic classifier for offline / dev / fallback mode."""
        lower = text.lower()

        # 1. Emergency Detection (English, Telugu, Hindi)
        emergency_signals = [
            # English
            "chest pain", "heart attack", "can't breathe", "cannot breathe", "difficulty breathing",
            "heavy bleeding", "bleeding profusely", "unconscious", "stroke", "seizure", "collapsed",
            "severe accident", "emergency", "ambulance",
            # Telugu / Tenglish
            "గుండె నొప్పి", "ఊపిరి ఆడటం లేదు", "స్పృహ తప్పింది", "రక్తం కారుతోంది", "అత్యవసరం",
            "gunde noppi", "oopiri aadatledu", "raktham",
            # Hindi / Hinglish
            "सीने में दर्द", "सांस नहीं", "बेहोश", "खून बह रहा", "दिल का दौरा", "इमरजेंसी",
            "seene me dard", "saans nahi", "behosh"
        ]
        if any(sig in lower for sig in emergency_signals):
            return TriageResult(
                is_emergency=True,
                intent="emergency",
                urgency="urgent",
                source="heuristic"
            )

        # 2. Cancellation Intent
        cancel_signals = ["cancel", "cancel appointment", "raddu", "vaddhu", "nahin aana", "nahi aana"]
        if any(sig in lower for sig in cancel_signals) and "reschedule" not in lower:
            return TriageResult(
                is_emergency=False,
                intent="cancel_booking",
                urgency="low",
                source="heuristic"
            )

        # 3. Check Slots Intent
        slot_signals = [
            "slot", "slots", "available", "schedule", "free timings", "timings available",
            "repu appointment", "kal appointment", "repu available", "kal available",
            "अपॉइंटमेंट", "समय", "उपलब्ध", "ఖాళీ", "సమయం"
        ]
        is_asking_slots = any(sig in lower for sig in slot_signals)
        is_booking = any(w in lower for w in [
            "book", "book my", "book slot", "reserve", "name is", "peru", "naam",
            "बुक", "బుకింగ్", "బుక్"
        ])

        if is_asking_slots and not is_booking:
            return TriageResult(
                is_emergency=False,
                intent="check_slots",
                urgency="low",
                source="heuristic"
            )
        elif is_booking:
            return TriageResult(
                is_emergency=False,
                intent="book_appointment",
                urgency="low",
                source="heuristic"
            )

        # 4. Clinic Info Intent
        clinic_info_signals = [
            "clinic timing", "timings", "clinic address", "location", "where is clinic",
            "fee", "fees", "consultation fee", "doctor timings", "is clinic open", "ekkada", "kahan"
        ]
        if any(sig in lower for sig in clinic_info_signals):
            return TriageResult(
                is_emergency=False,
                intent="clinic_info",
                urgency="low",
                source="heuristic"
            )

        # 5. Default General Inquiry
        return TriageResult(
            is_emergency=False,
            intent="general_inquiry",
            urgency="low",
            source="heuristic"
        )

# Global singleton
jev_service = JevService()
