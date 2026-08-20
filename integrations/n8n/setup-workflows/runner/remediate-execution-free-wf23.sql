\set ON_ERROR_STOP on

BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;
SET LOCAL search_path = pg_catalog, pg_temp, public;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '120s';
SELECT pg_advisory_xact_lock(hashtextextended(:'workflow_id', 0));

CREATE TEMP TABLE remediation_scope ON COMMIT DROP AS
SELECT :'workflow_id'::text AS workflow_id,
       :'project_id'::text AS project_id,
       :'folder_id'::text AS folder_id,
       :'workflow_name'::text AS workflow_name,
       :'incident_execution_id'::bigint AS incident_execution_id,
       convert_from(decode(:'expected_workflow_b64', 'base64'), 'UTF8')::jsonb AS expected_workflow,
       convert_from(decode(:'expected_history_b64', 'base64'), 'UTF8')::jsonb AS expected_history;

LOCK TABLE workflow_entity, execution_entity, execution_data, workflow_history,
           shared_workflow, workflows_tags IN SHARE ROW EXCLUSIVE MODE;

DO $remediation_preflight$
DECLARE
  scope remediation_scope%ROWTYPE;
  reference record;
  reference_count bigint;
  expected_count bigint;
  live_workflow jsonb;
  live_history jsonb;
  runtime_bindings jsonb;
BEGIN
  SELECT * INTO STRICT scope FROM remediation_scope;

  IF (SELECT count(*) FROM workflow_entity w
      WHERE w.id = scope.workflow_id
        AND w.name = scope.workflow_name
        AND w.active = false
        AND w."isArchived" = false
        AND w.description IS NULL
        AND w."staticData" IS NULL
        AND w."sourceWorkflowId" IS NULL
        AND w."triggerCount" = 0
        AND w."activeVersionId" IS NULL
        AND w."parentFolderId" = scope.folder_id
        AND w.meta->>'financeWorkflowCode' = 'MICROSOFT_OAUTH_REFRESH_PROOF'
        AND w.meta->>'migrationStatus' = 'READY_FOR_REVIEWED_MANUAL_IMPORT'
        AND w.meta->>'manualOnly' = 'true'
        AND w.meta->>'setupOnly' = 'true'
        AND w.meta->>'activationForbidden' = 'true'
        AND w.meta->>'scheduleForbidden' = 'true'
        AND w.meta->>'providerMutationScope' = 'NONE'
        AND w.meta->>'financeLedgerMutationForbidden' = 'true'
        AND w.meta->>'actualMutationForbidden' = 'true'
        AND w.meta->>'cashbackMutationForbidden' = 'true'
        AND NOT (w.settings::jsonb ? 'errorWorkflow')
        AND w.settings->>'saveDataErrorExecution' = 'none'
        AND w.settings->>'saveDataSuccessExecution' = 'none'
        AND w.nodes::text NOT LIKE '%BIND_%') <> 1 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_WORKFLOW_CONTRACT_MISMATCH';
  END IF;

  -- The pinned binder adds exactly these two status annotations and removes
  -- credential_id. Prove that exact runtime shape before normalizing the two
  -- annotations for comparison with the pinned source.
  SELECT w.meta::jsonb->'credentialBindings' INTO runtime_bindings
  FROM workflow_entity w
  WHERE w.id = scope.workflow_id;
  IF jsonb_typeof(runtime_bindings) IS DISTINCT FROM 'array'
     OR jsonb_array_length(runtime_bindings) <> 2 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_BINDER_ANNOTATION_MISMATCH';
  END IF;
  IF (SELECT count(*)
      FROM jsonb_array_elements(runtime_bindings) binding
      WHERE binding->>'type' IN ('microsoftOutlookOAuth2Api', 'microsoftOneDriveOAuth2Api')
        AND binding->'configured' = 'true'::jsonb
        AND binding->'action_required' = 'false'::jsonb
        AND NOT (binding ? 'credential_id')) <> 2 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_BINDER_ANNOTATION_MISMATCH';
  END IF;

  SELECT jsonb_build_object(
    'id', w.id,
    'name', w.name,
    'description', w.description,
    'active', w.active,
    'nodes', (
      SELECT jsonb_agg(
        CASE node->>'type'
          WHEN 'n8n-nodes-base.microsoftOutlook' THEN
            jsonb_set(node, '{credentials,microsoftOutlookOAuth2Api,id}', to_jsonb('BIND_OUTLOOK'::text), false)
          WHEN 'n8n-nodes-base.microsoftOneDrive' THEN
            jsonb_set(node, '{credentials,microsoftOneDriveOAuth2Api,id}', to_jsonb('BIND_ONEDRIVE'::text), false)
          ELSE node
        END ORDER BY ordinal
      )
      FROM jsonb_array_elements(w.nodes::jsonb) WITH ORDINALITY AS source_node(node, ordinal)
    ),
    'connections', w.connections::jsonb,
    'settings', w.settings::jsonb,
    'pinData', w."pinData"::jsonb,
    'meta', jsonb_set(
      w.meta::jsonb,
      '{credentialBindings}',
      (
        SELECT jsonb_agg(binding - 'configured' - 'action_required' - 'credential_id' ORDER BY ordinal)
        FROM jsonb_array_elements(w.meta::jsonb->'credentialBindings') WITH ORDINALITY AS source_binding(binding, ordinal)
      ),
      false
    ),
    'nodeGroups', w."nodeGroups"::jsonb,
    'isArchived', w."isArchived",
    'staticData', w."staticData"::jsonb,
    'sourceWorkflowId', w."sourceWorkflowId",
    'triggerCount', w."triggerCount",
    'activeVersionId', w."activeVersionId",
    'parentFolderId', w."parentFolderId"
  ) INTO live_workflow
  FROM workflow_entity w
  WHERE w.id = scope.workflow_id;
  IF live_workflow IS DISTINCT FROM scope.expected_workflow THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_CANONICAL_WORKFLOW_MISMATCH';
  END IF;

  IF (SELECT count(*) FROM folder f WHERE f.id = scope.folder_id
      AND f.name = '90 Platform & Admin' AND f."projectId" = scope.project_id) <> 1 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_FOLDER_MISMATCH';
  END IF;

  IF (SELECT count(*) FROM shared_workflow s WHERE s."workflowId" = scope.workflow_id
      AND s."projectId" = scope.project_id AND s.role = 'workflow:owner') <> 1
     OR (SELECT count(*) FROM shared_workflow s WHERE s."workflowId" = scope.workflow_id) <> 1 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_SHARE_MISMATCH';
  END IF;

  IF (SELECT string_agg(t.name, ',' ORDER BY t.name)
      FROM workflows_tags wt JOIN tag_entity t ON t.id = wt."tagId"
      WHERE wt."workflowId" = scope.workflow_id) <> 'finance,inactive,setup-required'
     OR (SELECT count(*) FROM workflows_tags wt WHERE wt."workflowId" = scope.workflow_id) <> 3 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_TAG_MISMATCH';
  END IF;

  IF (SELECT count(*) FROM workflow_entity other
      WHERE other.id <> scope.workflow_id
        AND (other.settings->>'errorWorkflow' = scope.workflow_id
             OR other.nodes::text LIKE '%' || scope.workflow_id || '%')) <> 0 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_WORKFLOW_DEPENDENCY_PRESENT';
  END IF;

  IF (SELECT count(*) FROM workflow_entity w
      CROSS JOIN LATERAL jsonb_array_elements(w.nodes::jsonb) node
      WHERE w.id = scope.workflow_id
        AND node->>'type' = 'n8n-nodes-base.microsoftOutlook'
        AND node->'parameters'->>'operation' = 'getAll'
        AND node->'parameters'->>'returnAll' = 'false'
        AND node->'parameters'->>'output' = 'fields'
        AND node->'parameters'->'fields' = '["id"]'::jsonb
        AND node->'parameters'->'options' = '{"downloadAttachments":false}'::jsonb
        AND length(node#>>'{credentials,microsoftOutlookOAuth2Api,id}') BETWEEN 8 AND 64) <> 1
     OR (SELECT count(*) FROM workflow_entity w
         CROSS JOIN LATERAL jsonb_array_elements(w.nodes::jsonb) node
         WHERE w.id = scope.workflow_id
           AND node->>'type' = 'n8n-nodes-base.microsoftOneDrive'
           AND node->'parameters'->>'operation' = 'getChildren'
           AND length(node#>>'{credentials,microsoftOneDriveOAuth2Api,id}') BETWEEN 8 AND 64) <> 1 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_PROVIDER_NODE_CONTRACT_MISMATCH';
  END IF;

  IF (SELECT count(*) FROM workflow_entity w
      CROSS JOIN LATERAL jsonb_array_elements(w.nodes::jsonb) node
      JOIN credentials_entity c ON c.id = node#>>'{credentials,microsoftOutlookOAuth2Api,id}'
      JOIN shared_credentials sc ON sc."credentialsId" = c.id
      WHERE w.id = scope.workflow_id AND node->>'type' = 'n8n-nodes-base.microsoftOutlook'
        AND c.type = 'microsoftOutlookOAuth2Api' AND sc."projectId" = scope.project_id
        AND sc.role = 'credential:owner') <> 1
     OR (SELECT count(*) FROM workflow_entity w
         CROSS JOIN LATERAL jsonb_array_elements(w.nodes::jsonb) node
         JOIN credentials_entity c ON c.id = node#>>'{credentials,microsoftOneDriveOAuth2Api,id}'
         JOIN shared_credentials sc ON sc."credentialsId" = c.id
         WHERE w.id = scope.workflow_id AND node->>'type' = 'n8n-nodes-base.microsoftOneDrive'
           AND c.type = 'microsoftOneDriveOAuth2Api' AND sc."projectId" = scope.project_id
           AND sc.role = 'credential:owner') <> 1 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_CREDENTIAL_BINDING_MISMATCH';
  END IF;

  -- n8n pruning already removed the incident execution. This cleanup is
  -- deliberately scoped to the resulting execution-free state and refuses
  -- every execution status, including soft-deleted rows.
  IF EXISTS (SELECT 1 FROM execution_entity e WHERE e."workflowId" = scope.workflow_id)
     OR EXISTS (SELECT 1 FROM execution_data d WHERE d."executionId" = scope.incident_execution_id) THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_EXECUTION_REFERENCE_PRESENT';
  END IF;

  IF (SELECT count(*) FROM workflow_history h JOIN workflow_entity w ON w.id = h."workflowId"
      WHERE h."workflowId" = scope.workflow_id
        AND h."versionId" = w."versionId"
        AND h.nodes::jsonb = w.nodes::jsonb
        AND h.connections::jsonb = w.connections::jsonb) <> 1
     OR (SELECT count(*) FROM workflow_history h WHERE h."workflowId" = scope.workflow_id) <> 1 THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_HISTORY_CONTRACT_MISMATCH';
  END IF;

  SELECT jsonb_build_object(
    'versionId', h."versionId",
    'workflowId', h."workflowId",
    'name', h.name,
    'description', h.description,
    'nodes', (
      SELECT jsonb_agg(
        CASE node->>'type'
          WHEN 'n8n-nodes-base.microsoftOutlook' THEN
            jsonb_set(node, '{credentials,microsoftOutlookOAuth2Api,id}', to_jsonb('BIND_OUTLOOK'::text), false)
          WHEN 'n8n-nodes-base.microsoftOneDrive' THEN
            jsonb_set(node, '{credentials,microsoftOneDriveOAuth2Api,id}', to_jsonb('BIND_ONEDRIVE'::text), false)
          ELSE node
        END ORDER BY ordinal
      )
      FROM jsonb_array_elements(h.nodes::jsonb) WITH ORDINALITY AS history_node(node, ordinal)
    ),
    'connections', h.connections::jsonb,
    'nodeGroups', h."nodeGroups"::jsonb,
    'authors', h.authors,
    'autosaved', h.autosaved
  ) INTO live_history
  FROM workflow_history h
  WHERE h."workflowId" = scope.workflow_id;
  IF live_history IS DISTINCT FROM scope.expected_history THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_CANONICAL_HISTORY_MISMATCH';
  END IF;

  FOR reference IN
    SELECT con.conrelid, rel.relname AS table_name, attr.attname AS column_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_attribute attr ON attr.attrelid = con.conrelid AND attr.attnum = con.conkey[1]
    WHERE con.contype = 'f' AND array_length(con.conkey, 1) = 1
      AND con.confrelid = 'workflow_entity'::regclass
  LOOP
    EXECUTE format('SELECT count(*) FROM %s WHERE %I = $1', reference.conrelid::regclass, reference.column_name)
      INTO reference_count USING scope.workflow_id;
    expected_count := CASE reference.table_name
      WHEN 'shared_workflow' THEN 1
      WHEN 'workflows_tags' THEN 3
      WHEN 'workflow_history' THEN 1
      WHEN 'execution_entity' THEN 0
      ELSE 0
    END;
    IF reference_count <> expected_count THEN
      RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_UNEXPECTED_WORKFLOW_REFERENCE';
    END IF;
  END LOOP;

END
$remediation_preflight$;

-- Transaction-local rollback backup of only the four exact workflow rows.
-- Execution payload data is never selected or copied.
CREATE TEMP TABLE wf23_backup_workflow ON COMMIT DROP AS
SELECT * FROM workflow_entity WHERE id = (SELECT workflow_id FROM remediation_scope);
CREATE TEMP TABLE wf23_backup_share ON COMMIT DROP AS
SELECT * FROM shared_workflow WHERE "workflowId" = (SELECT workflow_id FROM remediation_scope);
CREATE TEMP TABLE wf23_backup_tags ON COMMIT DROP AS
SELECT * FROM workflows_tags WHERE "workflowId" = (SELECT workflow_id FROM remediation_scope);
CREATE TEMP TABLE wf23_backup_history ON COMMIT DROP AS
SELECT * FROM workflow_history WHERE "workflowId" = (SELECT workflow_id FROM remediation_scope);

DO $remediation_delete$
DECLARE
  target_workflow text := (SELECT workflow_id FROM remediation_scope);
  affected integer;
BEGIN
  DELETE FROM workflow_history WHERE "workflowId" = target_workflow;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 1 THEN RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_HISTORY_DELETE_COUNT_MISMATCH'; END IF;

  DELETE FROM workflows_tags WHERE "workflowId" = target_workflow;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 3 THEN RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_TAG_DELETE_COUNT_MISMATCH'; END IF;

  DELETE FROM shared_workflow WHERE "workflowId" = target_workflow;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 1 THEN RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_SHARE_DELETE_COUNT_MISMATCH'; END IF;

  DELETE FROM workflow_entity WHERE id = target_workflow;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> 1 THEN RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_WORKFLOW_DELETE_COUNT_MISMATCH'; END IF;

  IF EXISTS (SELECT 1 FROM workflow_entity WHERE id = target_workflow)
     OR EXISTS (SELECT 1 FROM shared_workflow WHERE "workflowId" = target_workflow)
     OR EXISTS (SELECT 1 FROM workflows_tags WHERE "workflowId" = target_workflow)
     OR EXISTS (SELECT 1 FROM workflow_history WHERE "workflowId" = target_workflow)
     OR EXISTS (SELECT 1 FROM execution_entity WHERE "workflowId" = target_workflow)
     OR EXISTS (SELECT 1 FROM execution_data
               WHERE "executionId" = (SELECT incident_execution_id FROM remediation_scope)) THEN
    RAISE EXCEPTION 'WF23_EXECUTION_FREE_REMEDIATION_DELETE_READBACK_MISMATCH';
  END IF;
END
$remediation_delete$;

-- Both modes execute the exact same production transaction body above. An
-- absent variable defaults to off. PostgreSQL then derives a boolean from
-- literal equality with "on", so missing, malformed, false, true, 0, 1, and
-- every other value reach ROLLBACK. Only exact commit_authorized=on commits.
\if :{?commit_authorized}
\else
\set commit_authorized off
\endif
SELECT :'commit_authorized' = 'on' AS exact_commit_authorized \gset
\if :exact_commit_authorized
COMMIT;
\else
ROLLBACK;
\endif
