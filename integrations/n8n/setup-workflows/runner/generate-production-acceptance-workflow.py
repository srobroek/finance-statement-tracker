#!/usr/bin/env python3
"""Generate a private, inactive, finite scheduled production acceptance workflow."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

SOURCES = {'rak': '02-rakbank-live-cashback.json', 'ei': '04-ei-monthly-statement.json',
           'wio': '05-wio-monthly-statement.json'}
MAINTENANCE_ID = '10000000-0000-4000-8000-000000000025'


def build(root: Path, kind: str, at: datetime, source_commit: str, max_calls: int = 50) -> dict:
    if kind not in {*SOURCES, 'maintenance'} or not re.fullmatch(r'[a-f0-9]{40}', source_commit):
        raise ValueError('Reviewed source and supported acceptance kind are required')
    if at.tzinfo is None or at.utcoffset() != timedelta(0) or at.second or at.microsecond:
        raise ValueError('Schedule must be an exact UTC minute')
    if not 1 <= max_calls <= 100:
        raise ValueError('Maintenance call bound must be between 1 and 100')
    stamp = at.isoformat().replace('+00:00', 'Z')
    expiry = (at + timedelta(minutes=15)).isoformat().replace('+00:00', 'Z')
    local = at.astimezone(ZoneInfo('Asia/Dubai'))
    if kind in {'ei', 'wio'}:
        first = 1 if kind == 'ei' else 3
        if not first <= local.day <= first + 5:
            raise ValueError('Acceptance must remain inside the configured monthly cycle window')
    identity = hashlib.sha256(f'{source_commit}:{kind}:{stamp}'.encode()).hexdigest()[:16]
    if kind in SOURCES:
        source_path = root / 'integrations/n8n/workflows' / SOURCES[kind]
        raw = source_path.read_bytes()
        workflow = deepcopy(json.loads(raw))
        triggers = [n for n in workflow['nodes'] if n['type'] == 'n8n-nodes-base.scheduleTrigger']
        if len(triggers) != 1:
            raise ValueError('Expected exactly one canonical schedule')
        trigger = triggers[0]
        successors = workflow['connections'].pop(trigger['name'])
        source_sha256 = hashlib.sha256(raw).hexdigest()
    else:
        workflow = {'nodes': [], 'connections': {}, 'settings': {
            'executionOrder': 'v1', 'timezone': 'Asia/Dubai',
            'errorWorkflow': '10000000-0000-4000-8000-000000000016'}}
        trigger = {'id': 'acceptance-schedule', 'name': 'Finite Acceptance Schedule',
                   'type': 'n8n-nodes-base.scheduleTrigger', 'typeVersion': 1.2,
                   'position': [0, 0], 'parameters': {}}
        workflow['nodes'].append(trigger)
        successors = {'main': [[{'node': 'Clear Maintenance Caller Input', 'type': 'main', 'index': 0}]]}
        source_sha256 = None
        workflow['nodes'].extend([
            {'id': 'acceptance-empty-input', 'name': 'Clear Maintenance Caller Input',
             'type': 'n8n-nodes-base.set', 'typeVersion': 3.4, 'position': [360, 0],
             'parameters': {'assignments': {'assignments': []}, 'includeOtherFields': False, 'options': {}}},
            {'id': 'acceptance-maintenance', 'name': 'Run Reviewed Maintenance',
             'type': 'n8n-nodes-base.executeWorkflow', 'typeVersion': 1.2, 'position': [480, 0],
             'parameters': {'workflowId': {'__rl': True, 'value': MAINTENANCE_ID, 'mode': 'list'},
                            'options': {'waitForSubWorkflow': True},
                            'workflowInputs': {'mappingMode': 'defineBelow', 'value': {}, 'schema': [], 'matchingColumns': []}}},
            {'id': 'acceptance-maintenance-result', 'name': 'Bound Maintenance Iterations',
             'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [720, 0],
             'parameters': {'jsCode': f"if (typeof $json.complete !== 'boolean') throw new Error('MAINTENANCE_TERMINAL_BOOLEAN_REQUIRED');\nif (!$json.complete && $runIndex + 1 >= {max_calls}) throw new Error('ACCEPTANCE_MAINTENANCE_CALL_BOUND');\nreturn [{{json: $json}}];"}},
            {'id': 'acceptance-complete', 'name': 'Maintenance Complete', 'type': 'n8n-nodes-base.if',
             'typeVersion': 2.2, 'position': [960, 0], 'parameters': {'conditions': {
                 'options': {'caseSensitive': True, 'typeValidation': 'strict'}, 'combinator': 'and',
                 'conditions': [{'leftValue': '={{ $json.complete }}', 'rightValue': True,
                                 'operator': {'type': 'boolean', 'operation': 'true', 'singleValue': True}}]}}},
        ])
        workflow['connections'].update({
            'Clear Maintenance Caller Input': {'main': [[{'node': 'Run Reviewed Maintenance', 'type': 'main', 'index': 0}]]},
            'Run Reviewed Maintenance': {'main': [[{'node': 'Bound Maintenance Iterations', 'type': 'main', 'index': 0}]]},
            'Bound Maintenance Iterations': {'main': [[{'node': 'Maintenance Complete', 'type': 'main', 'index': 0}]]},
            'Maintenance Complete': {'main': [[], [{'node': 'Clear Maintenance Caller Input', 'type': 'main', 'index': 0}]]},
        })
    trigger['parameters'] = {'rule': {'interval': [{'field': 'cronExpression',
        'expression': f'{local.minute} {local.hour} {local.day} {local.month} *'}]}}
    gate = {'id': 'acceptance-time-bound', 'name': 'Verify Absolute Acceptance Window',
            'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [240, 0],
            'parameters': {'jsCode': f"if ($execution.mode !== 'production') throw new Error('ACCEPTANCE_SCHEDULE_REQUIRED');\nconst now = Date.now();\nif (now < Date.parse({json.dumps(stamp)}) || now >= Date.parse({json.dumps(expiry)})) throw new Error('ACCEPTANCE_WINDOW_CLOSED');\nreturn [{{json: {{}}}}];"}}
    workflow['nodes'].append(gate)
    workflow['connections'][trigger['name']] = {'main': [[{'node': gate['name'], 'type': 'main', 'index': 0}]]}
    workflow['connections'][gate['name']] = successors
    for key in ['versionId', 'activeVersionId', 'createdAt', 'updatedAt', 'pinData', 'shared', 'staticData']:
        workflow.pop(key, None)
    workflow.update(id=identity, name=f'Finance Scheduled Acceptance {kind.upper()} {stamp}', active=False)
    workflow.setdefault('settings', {}).update(timezone='Asia/Dubai', saveDataErrorExecution='none',
                                                saveDataSuccessExecution='none', saveManualExecutions=False)
    workflow['meta'] = {'financeAcceptance': {'source_commit': source_commit, 'source_sha256': source_sha256,
        'kind': kind, 'starts_at': stamp, 'expires_at': expiry, 'max_maintenance_calls': max_calls if kind == 'maintenance' else None,
        'requires_canonical_schedule_inactive': True, 'requires_deliberate_unpublish': True}}
    return workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--kind', choices=[*SOURCES, 'maintenance'], required=True)
    parser.add_argument('--at', required=True, help='Future exact minute in UTC, e.g. 2026-09-05T16:00:00Z')
    parser.add_argument('--max-maintenance-calls', type=int, default=50)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if subprocess.check_output(['git', '-C', str(args.source_root), 'rev-parse', 'HEAD'], text=True).strip() != args.source_commit:
        raise ValueError('Source checkout must match the reviewed commit')
    if subprocess.check_output(['git', '-C', str(args.source_root), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise ValueError('Source checkout has tracked modifications')
    at = datetime.fromisoformat(args.at.replace('Z', '+00:00'))
    if at <= datetime.now(timezone.utc):
        raise ValueError('Acceptance schedule must be in the future')
    workflow = build(args.source_root, args.kind, at, args.source_commit, args.max_maintenance_calls)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as target:
        target.write(json.dumps(workflow, indent=2) + '\n')
    print(json.dumps({'workflow_id': workflow['id'], 'active': False, 'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
