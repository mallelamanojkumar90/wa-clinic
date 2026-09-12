"""SQLAlchemy models for appointments, chat history, and webhook idempotency."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from app.database import Base

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_name = Column(String(255), nullable=False, default="")
    phone = Column(String(50), nullable=False, default="", index=True)
    slot = Column(DateTime(timezone=True), nullable=False, unique=True, index=True)
    status = Column(String(50), nullable=False, default="free", index=True)  # 'free', 'booked', 'cancelled'
    google_event_id = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_slot_status", "slot", "status"),
    )

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(50), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 'user', 'assistant', 'system', 'tool'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"

    wamid = Column(String(255), primary_key=True)
    phone = Column(String(50), nullable=True)
    processed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
