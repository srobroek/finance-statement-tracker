-- n8n 2.36.2.  Idempotent application workflow-folder placement.
-- Required: psql -v application_project_id='<exact project id>'.

\if :{?application_project_id}
\else
  \quit 3
\endif

BEGIN;

CREATE TEMP TABLE application_folder_context (
  project_id varchar(64) PRIMARY KEY
) ON COMMIT DROP;
INSERT INTO application_folder_context VALUES (:'application_project_id');

DO $$
DECLARE
  expected_project_id varchar(64);
BEGIN
  SELECT project_id INTO expected_project_id FROM application_folder_context;
  IF expected_project_id !~ '^[A-Za-z0-9_-]{8,64}$' THEN
    RAISE EXCEPTION 'APPLICATION_PROJECT_ID_INVALID';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM project p
    JOIN application_folder_context c ON c.project_id = p.id
  ) THEN
    RAISE EXCEPTION 'APPLICATION_PROJECT_NOT_FOUND';
  END IF;
END $$;

CREATE TEMP TABLE application_folder_contract (
  folder_id varchar(36) PRIMARY KEY,
  folder_name varchar(128) NOT NULL,
  parent_folder_id varchar(36),
  is_root boolean NOT NULL
) ON COMMIT DROP;
INSERT INTO application_folder_contract VALUES
  ('f1000000-0000-4000-8000-000000000100', 'Finance', NULL, TRUE),
  ('f1000000-0000-4000-8000-000000000190', 'Global', NULL, TRUE),
  ('f1000000-0000-4000-8000-000000000101', 'Account Reconciliation', 'f1000000-0000-4000-8000-000000000100', FALSE),
  ('f1000000-0000-4000-8000-000000000102', 'Cashback Sweep', 'f1000000-0000-4000-8000-000000000100', FALSE),
  ('f1000000-0000-4000-8000-000000000103', 'Shared', 'f1000000-0000-4000-8000-000000000100', FALSE),
  ('f1000000-0000-4000-8000-000000000191', 'Shared', 'f1000000-0000-4000-8000-000000000190', FALSE);

CREATE TEMP TABLE application_workflow_folder_contract (
  workflow_id varchar(36) PRIMARY KEY,
  folder_id varchar(36) NOT NULL
) ON COMMIT DROP;
INSERT INTO application_workflow_folder_contract VALUES
  ('10000000-0000-4000-8000-000000000001', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000002', 'f1000000-0000-4000-8000-000000000102'),
  ('10000000-0000-4000-8000-000000000003', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000004', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000005', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000009', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000010', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000011', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000012', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000013', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000014', 'f1000000-0000-4000-8000-000000000101'),
  ('10000000-0000-4000-8000-000000000015', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000016', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000017', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000018', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000019', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000020', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000021', 'f1000000-0000-4000-8000-000000000103'),
  ('10000000-0000-4000-8000-000000000024', 'f1000000-0000-4000-8000-000000000103');

DO $$
BEGIN
  IF (SELECT COUNT(*) FROM application_folder_contract) <> 6
     OR (SELECT COUNT(*) FROM application_folder_contract WHERE is_root) <> 2
     OR (SELECT COUNT(*) FROM application_folder_contract WHERE NOT is_root) <> 4 THEN
    RAISE EXCEPTION 'FOLDER_CONTRACT_COUNT_MISMATCH';
  END IF;
  IF (SELECT COUNT(*) FROM application_workflow_folder_contract) <> 19 THEN
    RAISE EXCEPTION 'WORKFLOW_FOLDER_MAP_COUNT_MISMATCH';
  END IF;
  IF EXISTS (
    SELECT 1 FROM application_workflow_folder_contract w
    LEFT JOIN application_folder_contract f ON f.folder_id = w.folder_id
    WHERE f.folder_id IS NULL
  ) THEN
    RAISE EXCEPTION 'WORKFLOW_FOLDER_REFERENCE_MISMATCH';
  END IF;
END $$;

CREATE TEMP TABLE application_workflow_activation_prestate (
  workflow_id varchar(36) PRIMARY KEY,
  active boolean NOT NULL,
  active_version_id varchar(36)
) ON COMMIT DROP;
INSERT INTO application_workflow_activation_prestate
SELECT w.id, w.active, w."activeVersionId"
FROM workflow_entity w
JOIN application_workflow_folder_contract c ON c.workflow_id = w.id
JOIN shared_workflow s ON s."workflowId" = w.id
JOIN application_folder_context context ON context.project_id = s."projectId";

DO $$
BEGIN
  IF (SELECT COUNT(*) FROM application_workflow_activation_prestate) <> 19 THEN
    RAISE EXCEPTION 'WORKFLOW_FOLDER_PROJECT_SCOPE_MISMATCH';
  END IF;
END $$;

INSERT INTO folder (id, name, "projectId", "parentFolderId", "createdAt", "updatedAt") VALUES
  ('f1000000-0000-4000-8000-000000000100', 'Finance', :'application_project_id', NULL, NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000190', 'Global', :'application_project_id', NULL, NOW(), NOW())
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name, "parentFolderId" = EXCLUDED."parentFolderId"
WHERE folder."projectId" = EXCLUDED."projectId"
  AND (folder.name IS DISTINCT FROM EXCLUDED.name
    OR folder."parentFolderId" IS DISTINCT FROM EXCLUDED."parentFolderId");

INSERT INTO folder (id, name, "projectId", "parentFolderId", "createdAt", "updatedAt") VALUES
  ('f1000000-0000-4000-8000-000000000101', 'Account Reconciliation', :'application_project_id', 'f1000000-0000-4000-8000-000000000100', NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000102', 'Cashback Sweep', :'application_project_id', 'f1000000-0000-4000-8000-000000000100', NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000103', 'Shared', :'application_project_id', 'f1000000-0000-4000-8000-000000000100', NOW(), NOW()),
  ('f1000000-0000-4000-8000-000000000191', 'Shared', :'application_project_id', 'f1000000-0000-4000-8000-000000000190', NOW(), NOW())
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name, "parentFolderId" = EXCLUDED."parentFolderId"
WHERE folder."projectId" = EXCLUDED."projectId"
  AND (folder.name IS DISTINCT FROM EXCLUDED.name
    OR folder."parentFolderId" IS DISTINCT FROM EXCLUDED."parentFolderId");

UPDATE workflow_entity w
SET "parentFolderId" = c.folder_id, "updatedAt" = NOW()
FROM application_workflow_folder_contract c
JOIN shared_workflow s ON s."workflowId" = c.workflow_id
WHERE w.id = c.workflow_id
  AND s."projectId" = :'application_project_id'
  AND w."parentFolderId" IS DISTINCT FROM c.folder_id;

DO $$
DECLARE
  expected_project_id varchar(64);
BEGIN
  SELECT project_id INTO expected_project_id FROM application_folder_context;
  IF (SELECT COUNT(*)
      FROM folder f
      JOIN application_folder_contract c ON c.folder_id = f.id
      WHERE f."projectId" = expected_project_id) <> 6
     OR EXISTS (
       SELECT 1 FROM application_folder_contract c
       LEFT JOIN folder f ON f.id = c.folder_id
         AND f."projectId" = expected_project_id
         AND f.name = c.folder_name
         AND f."parentFolderId" IS NOT DISTINCT FROM c.parent_folder_id
       WHERE f.id IS NULL
     ) THEN
    RAISE EXCEPTION 'FOLDER_READBACK_MISMATCH';
  END IF;
  IF (SELECT COUNT(*) FROM application_workflow_folder_contract) <> 19
     OR EXISTS (
       SELECT 1 FROM application_workflow_folder_contract c
       LEFT JOIN workflow_entity w ON w.id = c.workflow_id
       LEFT JOIN shared_workflow s ON s."workflowId" = w.id
         AND s."projectId" = expected_project_id
       WHERE w.id IS NULL OR s."workflowId" IS NULL
          OR w."parentFolderId" IS DISTINCT FROM c.folder_id
     ) THEN
    RAISE EXCEPTION 'WORKFLOW_FOLDER_READBACK_MISMATCH';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM application_workflow_activation_prestate p
    JOIN workflow_entity w ON w.id = p.workflow_id
    WHERE (w.active, w."activeVersionId")
      IS DISTINCT FROM (p.active, p.active_version_id)
  ) THEN
    RAISE EXCEPTION 'WORKFLOW_ACTIVATION_VERSION_CHANGED';
  END IF;
END $$;

COMMIT;
\echo 'WORKFLOW_FOLDER_PLACEMENT_COMMITTED'
