CREATE TABLE IF NOT EXISTS finance_document_operations (
  document_id text PRIMARY KEY,
  source_sha256 char(64) NOT NULL,
  document_profile text NOT NULL,
  requested_schema_version text NOT NULL,
  onedrive_item_id text NOT NULL,
  source_message_id text,
  source_attachment_id text,
  state text NOT NULL CHECK (state IN (
    'RECEIVED', 'VALIDATED', 'DECRYPTED', 'EXTRACTED',
    'SCHEMA_VALIDATED', 'READY_FOR_PARSE', 'COMMITTED',
    'QUARANTINED', 'UNSUPPORTED', 'PASSWORD_FAILED'
  )),
  attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  last_execution_id text,
  parser_version text,
  output_sha256 char(64),
  error_class text,
  error_detail_redacted text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_sha256, document_profile, requested_schema_version)
);

CREATE INDEX IF NOT EXISTS finance_document_operations_state_idx
  ON finance_document_operations (state, updated_at);

CREATE TABLE IF NOT EXISTS finance_provider_circuits (
  provider_code text PRIMARY KEY,
  state text NOT NULL CHECK (state IN ('CLOSED', 'OPEN', 'HALF_OPEN')),
  transient_failure_count integer NOT NULL DEFAULT 0,
  opened_at timestamptz,
  retry_after timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS finance_execution_failures (
  execution_id text PRIMARY KEY,
  workflow_id text NOT NULL,
  workflow_name text NOT NULL,
  error_class text NOT NULL,
  error_message_redacted text NOT NULL,
  execution_url text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  acknowledged_at timestamptz,
  replay_execution_id text
);
