-- n8n 2.36.2.  This is a bounded organization cutover for inactive workflows.
-- Required: psql -v finance_project_id='<exact project id>'.
-- Optional: psql -v finance_commit=true.  The default is a rehearsal and ROLLBACK.
-- The known disposable orphan 115 is backed up and retired only after the
-- canonical 024 export is present.  Active/published W15 is never changed.

\if :{?finance_project_id}
\else
  \quit 3
\endif
\if :{?finance_commit}
\else
  \set finance_commit false
\endif

BEGIN;

CREATE TEMP TABLE finance_organization_context (project_id varchar(36) PRIMARY KEY) ON COMMIT DROP;
INSERT INTO finance_organization_context VALUES (:'finance_project_id');

DO $$
DECLARE
  expected_project_id varchar(36);
BEGIN
  SELECT project_id INTO expected_project_id FROM finance_organization_context;
  IF expected_project_id !~ '^[0-9a-fA-F-]{36}$' THEN
    RAISE EXCEPTION 'FINANCE_PROJECT_ID_INVALID';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM project p
    JOIN finance_organization_context c ON c.project_id = p.id
  ) THEN
    RAISE EXCEPTION 'FINANCE_PROJECT_NOT_FOUND';
  END IF;
END $$;

-- The canonical six-folder hierarchy: two roots and four role-oriented children.
CREATE TEMP TABLE finance_folder_contract (
  folder_id varchar(36) PRIMARY KEY,
  folder_name varchar(128) NOT NULL,
  parent_folder_id varchar(36),
  is_root boolean NOT NULL
) ON COMMIT DROP;
INSERT INTO finance_folder_contract VALUES
  ('f1000000-0000-4000-8000-000000000100', 'Finance', NULL, TRUE),
  ('f1000000-0000-4000-8000-000000000190', 'Global', NULL, TRUE),
  ('f1000000-0000-4000-8000-000000000101', 'Account Reconciliation', 'f1000000-0000-4000-8000-000000000100', FALSE),
  ('f1000000-0000-4000-8000-000000000102', 'Cashback Sweep', 'f1000000-0000-4000-8000-000000000100', FALSE),
  ('f1000000-0000-4000-8000-000000000103', 'Shared', 'f1000000-0000-4000-8000-000000000100', FALSE),
  ('f1000000-0000-4000-8000-000000000191', 'Shared', 'f1000000-0000-4000-8000-000000000190', FALSE);

CREATE TEMP TABLE finance_workflow_contract (
  workflow_id varchar(36) PRIMARY KEY,
  current_name varchar(128) NOT NULL,
  target_name varchar(128) NOT NULL,
  folder_id varchar(36) NOT NULL
) ON COMMIT DROP;
INSERT INTO finance_workflow_contract VALUES
  ('10000000-0000-4000-8000-000000000001', 'Finance · Acquire Outlook Documents · Setup Required', 'Acquire Outlook Documents', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000002', 'Finance · RAKBANK Live Cashback · Setup Required', 'RAKBANK Live Cashback', 'f1000000-0000-4000-8000-000000000102'),
  ('10000000-0000-4000-8000-000000000003', 'Finance · Shared Deterministic Statement Pipeline · Setup Required', 'Shared Deterministic Statement Pipeline', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000004', 'Finance · EI Statement Cycle Poll · Setup Required', 'EI Statement Cycle Poll', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000005', 'Finance · Wio Statement Cycle Poll · Setup Required', 'Wio Statement Cycle Poll', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000006', 'Finance · RAK Monthly Statement · Setup Required', 'RAK Monthly Statement', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000007', 'Finance · SC Monthly Statement · Setup Required', 'SC Monthly Statement', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000008', 'Finance · SC Live Cashback · Setup Required', 'SC Live Cashback', 'f1000000-0000-4000-8000-000000000102'),
  ('10000000-0000-4000-8000-000000000009', 'Finance · Subscription Agent Proposal · Setup Required', 'Subscription Agent Proposal', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000010', 'Finance · Operations Status and Audited MCP Dispatch · Setup Required', 'Operations Status and Audited MCP Dispatch', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000011', 'Finance · Interactive Artifact Handoff · Setup Required', 'Interactive Artifact Handoff', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000012', 'Finance · Sweep Outlook Messages · Setup Required', 'Sweep Outlook Messages', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000013', 'Finance · Request Document Extraction · Setup Required', 'Request Document Extraction', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000014', 'Finance · Local PDF Extraction Ladder · Setup Required', 'Local PDF Extraction Ladder', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000015', 'Finance · Bounded MCP Facade · Setup Required', 'Bounded MCP Facade', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000016', 'Finance · Redacted Operations Error Handler · Setup Required', 'Redacted Operations Error Handler', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000017', 'Finance · Actual Outbox Recovery · Setup Required', 'Actual Outbox Recovery', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000018', 'Finance · Fenced Actual Writer Lease · Setup Required', 'Fenced Actual Writer Lease', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000019', 'Finance · Platform Data Table Bootstrap · Setup Required', 'Platform Data Table Bootstrap', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000020', 'Finance · Apply Prepared Actual Outbox · Setup Required', 'Apply Prepared Actual Outbox', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000021', 'Finance · Subscription Agent Adapter · Setup Required', 'Subscription Agent Adapter', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000024', 'Finance · Shared Monthly Statement Cycle', 'Shared Monthly Statement Cycle', 'f1000000-0000-4000-8000-000000000103');

-- Capture the disposable duplicate as a redacted retirement receipt.  The
-- complete JSON is held only in the transaction snapshot and is never emitted
-- by this script; ROLLBACK restores it byte-for-byte during rehearsal/failure.
CREATE TEMP TABLE finance_workflow_retirement (
  legacy_workflow_id varchar(36) PRIMARY KEY,
  replacement_workflow_id varchar(36) NOT NULL,
  project_id varchar(36) NOT NULL,
  workflow_row_json jsonb NOT NULL,
  shared_row_json jsonb,
  backup_md5 varchar(32) NOT NULL
) ON COMMIT DROP;
INSERT INTO finance_workflow_retirement
SELECT w.id,
       '10000000-0000-4000-8000-000000000024',
       s."projectId",
       to_jsonb(w),
       to_jsonb(s),
       md5(to_jsonb(w)::text || E'\n' || to_jsonb(s)::text)
FROM workflow_entity w
JOIN shared_workflow s ON s."workflowId" = w.id
WHERE w.id = '10000000-0000-4000-8000-000000000115'
  AND s."projectId" = :'finance_project_id';

DO $$
BEGIN
  IF (SELECT COUNT(*) FROM finance_folder_contract) <> 6
     OR (SELECT COUNT(*) FROM finance_folder_contract WHERE is_root) <> 2
     OR (SELECT COUNT(*) FROM finance_folder_contract WHERE NOT is_root) <> 4 THEN
    RAISE EXCEPTION 'FOLDER_CONTRACT_COUNT_MISMATCH';
  END IF;
  -- WORKFLOW_FOLDER_MAP_COUNT_MISMATCH is retained as the legacy guard name.
  IF (SELECT COUNT(*) FROM finance_workflow_contract) <> 22 THEN
    RAISE EXCEPTION 'WORKFLOW_CONTRACT_COUNT_MISMATCH';
  END IF;
  IF (SELECT COUNT(*) FROM finance_workflow_retirement) > 1 THEN
    RAISE EXCEPTION 'RETIREMENT_BACKUP_DUPLICATE';
  END IF;
  IF EXISTS (
    SELECT 1 FROM finance_workflow_retirement
    WHERE (workflow_row_json ->> 'active')::boolean
       OR NULLIF(workflow_row_json ->> 'activeVersionId', '') IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'ORPHAN_WORKFLOW_ACTIVE_OR_PUBLISHED';
  END IF;
  IF EXISTS (
    SELECT 1 FROM finance_workflow_contract c
    LEFT JOIN workflow_entity w ON w.id = c.workflow_id
    LEFT JOIN shared_workflow s ON s."workflowId" = w.id
    LEFT JOIN finance_organization_context context ON context.project_id = s."projectId"
    WHERE w.id IS NULL OR s."workflowId" IS NULL OR context.project_id IS NULL
      OR w.name NOT IN (
        c.current_name,
        c.target_name,
        regexp_replace(c.current_name, ' · Setup Required$', '')
      )
      OR c.target_name ILIKE ANY (ARRAY['%setup required%', '%spec_only%', '%inactive%', '%blocked%'])
  ) THEN
    RAISE EXCEPTION 'WORKFLOW_CONTRACT_SCOPE_OR_NAME_GUARD_FAILED';
  END IF;
  IF EXISTS (
    SELECT 1 FROM finance_workflow_contract c
    JOIN workflow_entity w ON w.id = c.workflow_id
    WHERE (c.workflow_id <> '10000000-0000-4000-8000-000000000015' AND w.active)
       OR (c.workflow_id <> '10000000-0000-4000-8000-000000000015' AND w."activeVersionId" IS NOT NULL)
  ) THEN
    RAISE EXCEPTION 'UNEXPECTED_ACTIVE_OR_PUBLISHED_WORKFLOW';
  END IF;
  IF (SELECT COUNT(*) FROM workflow_entity w JOIN finance_workflow_contract c ON c.workflow_id = w.id WHERE w.active = TRUE) <> 1
     OR (SELECT COUNT(*) FROM workflow_entity w JOIN finance_workflow_contract c ON c.workflow_id = w.id WHERE w."activeVersionId" IS NOT NULL) <> 1
     OR (SELECT w."activeVersionId" FROM workflow_entity w WHERE w.id = '10000000-0000-4000-8000-000000000015') IS DISTINCT FROM '1bd2090e-13e8-4427-bfe7-630c11bf0da5' THEN
    RAISE EXCEPTION 'ACTIVE_VERSION_TUPLE_GUARD_FAILED';
  END IF;
END $$;

-- Capture opaque rows before mutation.  These temporary records make a
-- rollback rehearsal exact without exposing workflow nodes or credentials.
CREATE TEMP TABLE finance_organization_prestate AS
SELECT 'workflow' AS kind, w.id AS row_id, to_jsonb(w) AS row_json
FROM workflow_entity w
JOIN finance_workflow_contract c ON c.workflow_id = w.id
JOIN shared_workflow ws ON ws."workflowId" = w.id
WHERE ws."projectId" = :'finance_project_id'
UNION ALL
SELECT 'retirement_workflow', w.id, to_jsonb(w)
FROM workflow_entity w
JOIN shared_workflow ws ON ws."workflowId" = w.id
WHERE w.id = '10000000-0000-4000-8000-000000000115'
  AND ws."projectId" = :'finance_project_id'
UNION ALL
SELECT 'shared_workflow', s."workflowId", to_jsonb(s)
FROM shared_workflow s
JOIN finance_workflow_contract c ON c.workflow_id = s."workflowId"
WHERE s."projectId" = :'finance_project_id'
UNION ALL
SELECT 'retirement_shared_workflow', s."workflowId", to_jsonb(s)
FROM shared_workflow s
WHERE s."workflowId" = '10000000-0000-4000-8000-000000000115'
  AND s."projectId" = :'finance_project_id'
UNION ALL
SELECT 'folder', f.id, to_jsonb(f)
FROM folder f
JOIN finance_folder_contract c ON c.folder_id = f.id
WHERE f."projectId" = :'finance_project_id'
UNION ALL
SELECT 'tag_edge', wt."workflowId" || ':' || wt."tagId", to_jsonb(wt)
FROM workflows_tags wt
JOIN finance_workflow_contract c ON c.workflow_id = wt."workflowId"
UNION ALL
SELECT 'retirement_tag_edge', wt."workflowId" || ':' || wt."tagId", to_jsonb(wt)
FROM workflows_tags wt
WHERE wt."workflowId" = '10000000-0000-4000-8000-000000000115';

SELECT 'ORGANIZATION_PRESTATE' AS receipt,
       COUNT(*) AS captured_rows,
       md5(COALESCE(string_agg(kind || '|' || row_id || '|' || row_json::text, E'\n' ORDER BY kind, row_id), '')) AS full_row_md5,
       md5(COALESCE(string_agg((row_json ->> 'id') || '|' || (row_json ->> 'name') || '|' || (row_json ->> 'parentFolderId') || '|' || (row_json ->> 'active') || '|' || (row_json ->> 'activeVersionId'), E'\n' ORDER BY row_id) FILTER (WHERE kind = 'workflow'), '')) AS logical_md5
FROM finance_organization_prestate;

SELECT 'RETIREMENT_BACKUP' AS receipt,
       COUNT(*) AS retired_workflow_rows,
       MIN(backup_md5) AS backup_md5,
       BOOL_OR(legacy_workflow_id = '10000000-0000-4000-8000-000000000115') AS orphan_captured,
       BOOL_OR(replacement_workflow_id = '10000000-0000-4000-8000-000000000024') AS replacement_bound
FROM finance_workflow_retirement;

-- Tag identities are stable contracts.  Existing tags are never renamed.
INSERT INTO tag_entity (id, name, "createdAt", "updatedAt") VALUES
  ('fin0000000000001', 'finance', NOW(), NOW()),
  ('fin0000000000002', 'setup-required', NOW(), NOW()),
  ('fin0000000000003', 'inactive', NOW(), NOW()),
  ('fin0000000000004', 'active', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM tag_entity
    WHERE (id, name) NOT IN (
      ('fin0000000000001', 'finance'),
      ('fin0000000000002', 'setup-required'),
      ('fin0000000000003', 'inactive'),
      ('fin0000000000004', 'active')
    ) AND id IN ('fin0000000000001', 'fin0000000000002', 'fin0000000000003', 'fin0000000000004')
  ) THEN
    RAISE EXCEPTION 'TAG_ID_NAME_CONFLICT';
  END IF;
END $$;

-- Create/reuse roots first so the child parent references are valid.
INSERT INTO folder (id, name, "projectId", "parentFolderId", "createdAt", "updatedAt") VALUES
  ('f1000000-0000-4000-8000-000000000100', 'Finance', :'finance_project_id', NULL, NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000190', 'Global', :'finance_project_id', NULL, NOW(), NOW())
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name, "parentFolderId" = EXCLUDED."parentFolderId"
WHERE folder."projectId" = EXCLUDED."projectId"
  AND (folder.name IS DISTINCT FROM EXCLUDED.name OR folder."parentFolderId" IS DISTINCT FROM EXCLUDED."parentFolderId");

INSERT INTO folder (id, name, "projectId", "parentFolderId", "createdAt", "updatedAt") VALUES
  ('f1000000-0000-4000-8000-000000000101', 'Account Reconciliation', :'finance_project_id', 'f1000000-0000-4000-8000-000000000100', NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000102', 'Cashback Sweep', :'finance_project_id', 'f1000000-0000-4000-8000-000000000100', NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000103', 'Shared', :'finance_project_id', 'f1000000-0000-4000-8000-000000000100', NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000191', 'Shared', :'finance_project_id', 'f1000000-0000-4000-8000-000000000190', NOW(), NOW())
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name, "parentFolderId" = EXCLUDED."parentFolderId"
WHERE folder."projectId" = EXCLUDED."projectId"
  AND (folder.name IS DISTINCT FROM EXCLUDED.name OR folder."parentFolderId" IS DISTINCT FROM EXCLUDED."parentFolderId");

-- Move and normalize only the exact 22 rows.  The guarded update cannot alter
-- active/version state and is a no-op on the second invocation.
UPDATE workflow_entity w
SET name = c.target_name, "parentFolderId" = c.folder_id, "updatedAt" = NOW()
FROM finance_workflow_contract c
JOIN shared_workflow s ON s."workflowId" = c.workflow_id
WHERE w.id = c.workflow_id
  AND s."projectId" = :'finance_project_id'
  AND (w.name IS DISTINCT FROM c.target_name OR w."parentFolderId" IS DISTINCT FROM c.folder_id);

-- Retire only the inactive disposable duplicate after the canonical source row
-- is present and its opaque prestate is captured.  The transaction remains the
-- rollback boundary; a rehearsal therefore leaves the old row untouched.
DELETE FROM workflows_tags wt
USING finance_workflow_retirement r
WHERE wt."workflowId" = r.legacy_workflow_id;

DELETE FROM shared_workflow s
USING finance_workflow_retirement r
WHERE s."workflowId" = r.legacy_workflow_id
  AND s."projectId" = r.project_id;

DELETE FROM workflow_entity w
USING finance_workflow_retirement r
WHERE w.id = r.legacy_workflow_id
  AND w.active = FALSE
  AND w."activeVersionId" IS NULL;

DELETE FROM workflows_tags wt
USING finance_workflow_contract c
WHERE wt."workflowId" = c.workflow_id
  AND c.workflow_id = '10000000-0000-4000-8000-000000000015'
  AND wt."tagId" = 'fin0000000000003';

INSERT INTO workflows_tags ("workflowId", "tagId")
VALUES ('10000000-0000-4000-8000-000000000015', 'fin0000000000004')
ON CONFLICT DO NOTHING;

INSERT INTO workflows_tags ("workflowId", "tagId")
SELECT c.workflow_id, t.tag_id
FROM finance_workflow_contract c
CROSS JOIN (VALUES ('fin0000000000001'), ('fin0000000000002')) AS t(tag_id)
ON CONFLICT DO NOTHING;

INSERT INTO workflows_tags ("workflowId", "tagId")
SELECT c.workflow_id, 'fin0000000000003'
FROM finance_workflow_contract c
WHERE c.workflow_id <> '10000000-0000-4000-8000-000000000015'
ON CONFLICT DO NOTHING;

-- Remove only the known legacy flat folders once no target workflow references
-- them.  Unrelated folders in the project are not touched.
DELETE FROM folder f
WHERE f."projectId" = :'finance_project_id'
  AND f.id IN ('f1000000-0000-4000-8000-000000000001', 'f1000000-0000-4000-8000-000000000002', 'f1000000-0000-4000-8000-000000000003', 'f1000000-0000-4000-8000-000000000004', 'f1000000-0000-4000-8000-000000000005', 'f1000000-0000-4000-8000-000000000006', 'f1000000-0000-4000-8000-000000000007', 'f1000000-0000-4000-8000-000000000090')
  AND NOT EXISTS (SELECT 1 FROM workflow_entity w WHERE w."parentFolderId" = f.id)
  AND NOT EXISTS (SELECT 1 FROM folder child WHERE child."parentFolderId" = f.id);

DO $$
DECLARE
  expected_project_id varchar(36);
BEGIN
  SELECT project_id INTO expected_project_id FROM finance_organization_context;
  IF EXISTS (
    SELECT 1
    FROM workflow_entity w
    JOIN shared_workflow s ON s."workflowId" = w.id
    WHERE w.id = '10000000-0000-4000-8000-000000000115'
      AND s."projectId" = expected_project_id
  ) THEN
    RAISE EXCEPTION 'ORPHAN_WORKFLOW_REMAINS';
  END IF;
  IF (SELECT COUNT(*) FROM workflow_entity w
      JOIN shared_workflow s ON s."workflowId" = w.id
      JOIN finance_workflow_contract c ON c.workflow_id = w.id
      WHERE s."projectId" = expected_project_id) <> 22 THEN
    RAISE EXCEPTION 'CANONICAL_WORKFLOW_ROSTER_COUNT_MISMATCH';
  END IF;
  IF EXISTS (
    SELECT 1 FROM folder
    WHERE "projectId" = expected_project_id
      AND id IN ('f1000000-0000-4000-8000-000000000001', 'f1000000-0000-4000-8000-000000000002', 'f1000000-0000-4000-8000-000000000003', 'f1000000-0000-4000-8000-000000000004', 'f1000000-0000-4000-8000-000000000005', 'f1000000-0000-4000-8000-000000000006', 'f1000000-0000-4000-8000-000000000007', 'f1000000-0000-4000-8000-000000000090')
  ) THEN
    RAISE EXCEPTION 'LEGACY_FOLDER_REMAINS';
  END IF;
  IF (SELECT COUNT(*) FROM folder f JOIN finance_folder_contract c ON c.folder_id = f.id WHERE f."projectId" = expected_project_id) <> 6
     OR EXISTS (
       SELECT 1 FROM finance_folder_contract c
       LEFT JOIN folder f ON f.id = c.folder_id
         AND f."projectId" = expected_project_id
         AND f.name = c.folder_name
         AND f."parentFolderId" IS NOT DISTINCT FROM c.parent_folder_id
       WHERE f.id IS NULL
     ) THEN
    RAISE EXCEPTION 'FOLDER_READBACK_MISMATCH';
  END IF;
  IF (SELECT COUNT(*) FROM finance_workflow_contract) <> 22
     OR EXISTS (
       SELECT 1 FROM finance_workflow_contract c
       JOIN workflow_entity w ON w.id = c.workflow_id
       WHERE w.name IS DISTINCT FROM c.target_name
          OR w."parentFolderId" IS DISTINCT FROM c.folder_id
     ) THEN
    -- WORKFLOW_FOLDER_READBACK_MISMATCH is the legacy placement guard name.
    RAISE EXCEPTION 'WORKFLOW_NAME_OR_FOLDER_READBACK_MISMATCH';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM finance_workflow_contract c
    JOIN workflow_entity w ON w.id = c.workflow_id
    JOIN finance_organization_prestate p ON p.kind = 'workflow' AND p.row_id = w.id
    WHERE (w.active, w."activeVersionId") IS DISTINCT FROM ((p.row_json ->> 'active')::boolean, p.row_json ->> 'activeVersionId')
  ) THEN
    RAISE EXCEPTION 'VERSION_TUPLE_READBACK_MISMATCH';
  END IF;
  IF (SELECT COUNT(*) FROM workflow_entity w JOIN finance_workflow_contract c ON c.workflow_id = w.id WHERE w.active) <> 1
     OR (SELECT COUNT(*) FROM workflow_entity w JOIN finance_workflow_contract c ON c.workflow_id = w.id WHERE w."activeVersionId" IS NOT NULL) <> 1
     OR (SELECT w."activeVersionId" FROM workflow_entity w WHERE w.id = '10000000-0000-4000-8000-000000000015') IS DISTINCT FROM '1bd2090e-13e8-4427-bfe7-630c11bf0da5' THEN
    RAISE EXCEPTION 'ACTIVE_PUBLISHED_READBACK_MISMATCH';
  END IF;
  IF (SELECT COUNT(*) FROM workflows_tags wt JOIN finance_workflow_contract c ON c.workflow_id = wt."workflowId" WHERE wt."tagId" = 'fin0000000000001') <> 22
     OR (SELECT COUNT(*) FROM workflows_tags wt JOIN finance_workflow_contract c ON c.workflow_id = wt."workflowId" WHERE wt."tagId" = 'fin0000000000002') <> 22
     OR (SELECT COUNT(*) FROM workflows_tags wt JOIN finance_workflow_contract c ON c.workflow_id = wt."workflowId" WHERE wt."tagId" = 'fin0000000000003') <> 21
     OR (SELECT COUNT(*) FROM workflows_tags wt JOIN finance_workflow_contract c ON c.workflow_id = wt."workflowId" WHERE wt."tagId" = 'fin0000000000004') <> 1
     OR EXISTS (SELECT 1 FROM workflows_tags WHERE "workflowId" = '10000000-0000-4000-8000-000000000015' AND "tagId" = 'fin0000000000003') THEN
    RAISE EXCEPTION 'TAG_EDGE_READBACK_MISMATCH';
  END IF;
END $$;

SELECT 'ORGANIZATION_POSTSTATE' AS receipt,
       COUNT(*) AS workflow_count,
       COUNT(*) FILTER (WHERE w.active) AS active_count,
       COUNT(*) FILTER (WHERE w."activeVersionId" IS NOT NULL) AS published_count,
       (SELECT COUNT(*) FROM finance_workflow_retirement) AS retired_workflow_count,
       (SELECT MIN(backup_md5) FROM finance_workflow_retirement) AS retirement_backup_md5,
       md5(COALESCE(string_agg(to_jsonb(w)::text, E'\n' ORDER BY w.id), '')) AS full_row_md5,
       md5(COALESCE(string_agg(jsonb_build_object('id', w.id, 'name', w.name, 'parentFolderId', w."parentFolderId", 'active', w.active, 'activeVersionId', w."activeVersionId")::text, E'\n' ORDER BY w.id), '')) AS logical_md5
FROM workflow_entity w
JOIN finance_workflow_contract c ON c.workflow_id = w.id;

\if :finance_commit
  COMMIT;
  \echo 'ORGANIZATION_COMMITTED'
\else
  ROLLBACK;
  \echo 'ORGANIZATION_REHEARSAL_ROLLED_BACK'
\endif
