-- =============================================================================
-- Supabase Schema for WhatsApp Clinic Receptionist (Dr. Rao's Clinic)
-- Run this script in the Supabase SQL Editor: https://supabase.com/dashboard/project/_/sql
-- =============================================================================

-- 1. Appointments Table
CREATE TABLE IF NOT EXISTS appointments (
    id BIGSERIAL PRIMARY KEY,
    patient_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    slot TIMESTAMPTZ NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'free', -- 'free', 'booked', 'cancelled'
    google_event_id TEXT DEFAULT NULL,   -- Associated Google Calendar Event ID
    notes TEXT DEFAULT NULL,
    reminder_24h_sent BOOLEAN NOT NULL DEFAULT FALSE,
    reminder_2h_sent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Migration support for existing appointments table
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_2h_sent BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_appointments_slot_status ON appointments (slot, status);
CREATE INDEX IF NOT EXISTS idx_appointments_phone ON appointments (phone);

-- 2. Chat Conversation History (Persistent Memory across workers/restarts)
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    phone TEXT NOT NULL,
    role TEXT NOT NULL, -- 'user', 'assistant', 'system', 'tool'
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_phone ON chat_messages (phone, created_at DESC);

-- 3. Webhook Deduplication Table (prevents duplicate processing on network retry)
CREATE TABLE IF NOT EXISTS processed_webhooks (
    wamid TEXT PRIMARY KEY,
    phone TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Helper trigger for automatic updated_at timestamp
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_appointments_modtime ON appointments;
CREATE TRIGGER update_appointments_modtime
    BEFORE UPDATE ON appointments
    FOR EACH ROW
    EXECUTE FUNCTION update_modified_column();
