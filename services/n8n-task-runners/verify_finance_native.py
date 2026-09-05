"""Verify the installed native runner security boundary in a fresh process.

Usage: runner-venv/bin/python -I verify_finance_native.py REPO_ROOT [external_allow]
The finance module comes from the image site-packages/.pth, never REPO_ROOT.
"""
import json
import os
from pathlib import Path
import sys
from multiprocessing.connection import Connection

from src.config.security_config import SecurityConfig
from src.constants import BUILTINS_DENY_DEFAULT, PIPE_MSG_PREFIX_LENGTH
from src.task_analyzer import TaskAnalyzer
from src.task_executor import TaskExecutor

root = Path(sys.argv[1]).resolve()
allow = set((sys.argv[2] if len(sys.argv) > 2 else 'finance_tracker,jsonschema_specifications').split(','))
workflow = json.loads((root / 'integrations/n8n/workflows/02-rakbank-live-cashback.json').read_text())
code = next(node['parameters']['pythonCode'] for node in workflow['nodes'] if 'pythonCode' in node.get('parameters', {}))
fixture = json.loads((root / 'tests/fixtures/rakbank-card-transaction.json').read_text())
envelope = {'messages': [fixture], 'matched_count': 1, 'source': 'OUTLOOK',
    'source_code': 'RAKBANK_CARD_TRANSACTION', 'completed_at': '2026-08-18T00:00:00Z',
    'cursor': '2026-08-18T00:00:00Z', 'window_start': '2026-08-17T00:00:00Z'}
security = SecurityConfig(stdlib_allow=set(), external_allow=allow,
    builtins_deny=set(BUILTINS_DENY_DEFAULT.split(',')), runner_env_deny=True,
    allow_transitive_imports=False)
TaskAnalyzer(security).validate(code)
read_fd, write_fd = os.pipe()
write_conn = Connection(write_fd, readable=False, writable=True)
stderr = sys.stderr
TaskExecutor._all_items(code, [{'json': envelope}], write_conn, security)
write_conn._handle = None  # Upstream _put_result/_put_error closes the raw descriptor.
sys.stderr = stderr
size = int.from_bytes(os.read(read_fd, PIPE_MSG_PREFIX_LENGTH), 'big')
body = b''
while len(body) < size:
    chunk = os.read(read_fd, size - len(body))
    if not chunk:
        raise AssertionError('truncated runner response')
    body += chunk
os.close(read_fd)
message = json.loads(body)
if 'result' not in message:
    print(json.dumps(message))
    raise SystemExit(1)
result = message['result'][0]['json']
assert result['accepted_count'] == 1, result
assert len(result['events']) == 1, result
event = result['events'][0]
assert event['amount_aed'] == '41.49', event
assert event['card_code'] == 'RAK_WORLD', event
assert result['message_dispositions'][0]['status'] == 'ACCEPTED', result
print(json.dumps({'status': 'PASS', 'external_allow': sorted(allow),
    'transitive_imports': False, 'accepted_count': result['accepted_count'],
    'amount_aed': event['amount_aed'], 'bucket_code': event['bucket_code'],
    'native_ast_validation': True, 'native_all_items_executor': True}))
