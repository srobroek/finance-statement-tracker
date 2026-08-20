DROP TABLE IF EXISTS wf23_commit_gate_probe;
CREATE TABLE wf23_commit_gate_probe (marker integer NOT NULL);
BEGIN;
INSERT INTO wf23_commit_gate_probe VALUES (1);
\ir psql-wf23-exact-commit-gate.sql
SELECT count(*) FROM wf23_commit_gate_probe;
DROP TABLE wf23_commit_gate_probe;
