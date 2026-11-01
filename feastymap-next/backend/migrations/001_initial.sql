-- Apply with: psql "$DATABASE_URL" -f backend/migrations/001_initial.sql
-- Stores user-owned planning data. Provider responses are referenced by place ID only.
CREATE TABLE IF NOT EXISTS user_preferences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS itineraries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  city TEXT NOT NULL DEFAULT 'Pondicherry',
  plan_date DATE NOT NULL,
  request JSONB NOT NULL,
  summary JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS itineraries_user_created_idx ON itineraries(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS itinerary_stops (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  itinerary_id UUID NOT NULL REFERENCES itineraries(id) ON DELETE CASCADE,
  position SMALLINT NOT NULL CHECK (position > 0),
  provider TEXT NOT NULL,
  provider_place_id TEXT NOT NULL,
  category TEXT NOT NULL,
  scheduled_start TIMESTAMPTZ,
  visit_duration_minutes SMALLINT CHECK (visit_duration_minutes > 0),
  UNIQUE (itinerary_id, position)
);
ALTER TABLE itinerary_stops ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'activity';
CREATE TABLE IF NOT EXISTS source_retrievals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  provider_reference TEXT NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  freshness TEXT NOT NULL,
  verification_status TEXT NOT NULL,
  permitted_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
