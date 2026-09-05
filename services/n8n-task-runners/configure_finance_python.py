"""Permit only the packaged finance module in Python task code."""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
config = json.loads(path.read_text())
python = [runner for runner in config['task-runners'] if runner['runner-type'] == 'python']
if len(python) != 1:
    raise SystemExit('Exactly one Python runner is required')
python[0].setdefault('env-overrides', {})['N8N_RUNNERS_EXTERNAL_ALLOW'] = 'finance_tracker,jsonschema_specifications'
path.write_text(json.dumps(config, indent=2) + '\n')
