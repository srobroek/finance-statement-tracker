"""Generate or check the persistent finance project backlog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_tracker.project_backlog import (  # noqa: E402
    BACKLOG_DOC_PATH,
    BACKLOG_PATH,
    TRANSCRIPT_PATH,
    load_and_build,
    render_markdown,
    validate_backlog,
    write_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate and require generated files to be current")
    args = parser.parse_args()

    payload = load_and_build()
    transcript_ids = {
        row["id"]
        for row in json.loads(TRANSCRIPT_PATH.read_text(encoding="utf-8"))["requirements"]
    }
    errors = validate_backlog(payload, transcript_ids)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    expected_json = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    expected_markdown = render_markdown(payload)
    if args.check:
        if not BACKLOG_PATH.exists() or BACKLOG_PATH.read_text(encoding="utf-8") != expected_json:
            print(f"stale generated backlog: {BACKLOG_PATH}", file=sys.stderr)
            return 1
        if not BACKLOG_DOC_PATH.exists() or BACKLOG_DOC_PATH.read_text(encoding="utf-8") != expected_markdown:
            print(f"stale generated backlog document: {BACKLOG_DOC_PATH}", file=sys.stderr)
            return 1
    else:
        write_outputs(payload)
    print(f"project backlog valid: {len(payload['tasks'])} tasks, {len(payload['ordered_executable_queue'])} queued")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
