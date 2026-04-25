-- ============================================================
-- Plataforma Omnicanal — Schema Supabase
-- Ejecutar en: Supabase Dashboard → SQL Editor → New query
-- ============================================================

-- Agentes del sistema (usuarios con login)
CREATE TABLE IF NOT EXISTS agents (
  id                  TEXT PRIMARY KEY,
  nombre              TEXT NOT NULL,
  rol                 TEXT NOT NULL DEFAULT 'agente',
  area                TEXT NOT NULL DEFAULT 'ventas',
  email               TEXT UNIQUE NOT NULL,
  password_hash       TEXT NOT NULL,
  system_prompt       TEXT DEFAULT '',
  permisos_modelo     TEXT[] DEFAULT ARRAY['haiku','sonnet'],
  limite_tokens_dia   INTEGER DEFAULT 200000,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Blob de datos operativos: casos, mensajes, recordatorios, métricas, tuning
-- (un único JSONB row — simple y suficiente para el mockup)
CREATE TABLE IF NOT EXISTS storage_blob (
  id    INTEGER PRIMARY KEY DEFAULT 1,
  data  JSONB NOT NULL DEFAULT '{}'
);
INSERT INTO storage_blob (id, data)
VALUES (1, '{"messages":[],"cases":[],"tuning_reviews":[],"metrics":{"token_usage":[]},"reminders":[]}')
ON CONFLICT (id) DO NOTHING;

-- Blob de CRM: agencias, pasajeros, tags, channel_rules
CREATE TABLE IF NOT EXISTS crm_blob (
  id    INTEGER PRIMARY KEY DEFAULT 1,
  data  JSONB NOT NULL DEFAULT '{}'
);
INSERT INTO crm_blob (id, data)
VALUES (1, '{"agencias":[],"pasajeros":[],"tags_workspace":[],"channel_rules_default":{}}')
ON CONFLICT (id) DO NOTHING;
