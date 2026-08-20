\set ON_ERROR_STOP on

DROP TABLE IF EXISTS execution_data, execution_entity, workflow_history,
  workflows_tags, tag_entity, shared_workflow, shared_credentials,
  credentials_entity, folder, workflow_entity CASCADE;

CREATE TABLE workflow_entity (
  id text PRIMARY KEY,
  name text NOT NULL,
  description text,
  active boolean NOT NULL,
  nodes jsonb NOT NULL,
  connections jsonb NOT NULL,
  settings jsonb NOT NULL,
  "pinData" jsonb,
  meta jsonb,
  "nodeGroups" jsonb,
  "isArchived" boolean NOT NULL,
  "staticData" jsonb,
  "sourceWorkflowId" text,
  "triggerCount" integer NOT NULL,
  "activeVersionId" text,
  "parentFolderId" text,
  "versionId" text NOT NULL
);
CREATE TABLE folder (id text PRIMARY KEY, name text NOT NULL, "projectId" text NOT NULL);
CREATE TABLE shared_workflow (
  "workflowId" text REFERENCES workflow_entity(id),
  "projectId" text NOT NULL,
  role text NOT NULL
);
CREATE TABLE tag_entity (id text PRIMARY KEY, name text NOT NULL);
CREATE TABLE workflows_tags (
  "workflowId" text REFERENCES workflow_entity(id),
  "tagId" text REFERENCES tag_entity(id)
);
CREATE TABLE credentials_entity (id text PRIMARY KEY, type text NOT NULL);
CREATE TABLE shared_credentials ("credentialsId" text, "projectId" text, role text);
CREATE TABLE workflow_history (
  "versionId" text NOT NULL,
  "workflowId" text REFERENCES workflow_entity(id),
  name text,
  description text,
  nodes jsonb NOT NULL,
  connections jsonb NOT NULL,
  "nodeGroups" jsonb,
  authors text,
  autosaved boolean
);
CREATE TABLE execution_entity (
  id bigint PRIMARY KEY,
  "workflowId" text REFERENCES workflow_entity(id)
);
CREATE TABLE execution_data (
  "executionId" bigint PRIMARY KEY REFERENCES execution_entity(id)
);

WITH runtime AS (
  SELECT convert_from(decode(:'runtime_workflow_b64', 'base64'), 'UTF8')::jsonb value
)
INSERT INTO workflow_entity
SELECT value->>'id', value->>'name', value->>'description', (value->>'active')::boolean,
       value->'nodes', value->'connections', value->'settings', value->'pinData', value->'meta',
       value->'nodeGroups', (value->>'isArchived')::boolean,
       NULLIF(value->'staticData', 'null'::jsonb),
       value->>'sourceWorkflowId', (value->>'triggerCount')::integer,
       value->>'activeVersionId', value->>'parentFolderId', :'version_id'
FROM runtime;

INSERT INTO folder VALUES (:'folder_id', '90 Platform & Admin', :'project_id');
INSERT INTO shared_workflow VALUES (:'workflow_id', :'project_id', 'workflow:owner');
INSERT INTO tag_entity VALUES
  ('tag-finance', 'finance'), ('tag-inactive', 'inactive'), ('tag-setup', 'setup-required');
INSERT INTO workflows_tags VALUES
  (:'workflow_id', 'tag-finance'), (:'workflow_id', 'tag-inactive'), (:'workflow_id', 'tag-setup');
INSERT INTO credentials_entity VALUES
  ('outlookCred123', 'microsoftOutlookOAuth2Api'),
  ('onedriveCred123', 'microsoftOneDriveOAuth2Api');
INSERT INTO shared_credentials VALUES
  ('outlookCred123', :'project_id', 'credential:owner'),
  ('onedriveCred123', :'project_id', 'credential:owner');

WITH runtime AS (
  SELECT convert_from(decode(:'runtime_history_b64', 'base64'), 'UTF8')::jsonb value
)
INSERT INTO workflow_history
SELECT value->>'versionId', value->>'workflowId', value->>'name', value->>'description',
       value->'nodes', value->'connections', value->'nodeGroups', value->>'authors',
       (value->>'autosaved')::boolean
FROM runtime;

\ir ../../integrations/n8n/setup-workflows/runner/remediate-execution-free-wf23.sql

SELECT
  (SELECT count(*) FROM workflow_entity WHERE id = :'workflow_id') || '|' ||
  (SELECT count(*) FROM workflow_history WHERE "workflowId" = :'workflow_id') || '|' ||
  (SELECT count(*) FROM shared_workflow WHERE "workflowId" = :'workflow_id') || '|' ||
  (SELECT count(*) FROM workflows_tags WHERE "workflowId" = :'workflow_id') || '|' ||
  (SELECT count(*) FROM execution_entity WHERE "workflowId" = :'workflow_id') || '|' ||
  (SELECT count(*) FROM execution_data WHERE "executionId" = :'incident_execution_id');
