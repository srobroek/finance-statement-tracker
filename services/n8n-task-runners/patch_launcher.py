#!/usr/bin/env python3
"""Apply the sole reviewed launcher source dependency update, fail closed."""

from __future__ import annotations

import hashlib
import pathlib
import sys


SOURCE_SHA256 = {
    "go.mod": "1cfaf3c04029102d9b1d43ba52ff00668696b2ea0a125534667ab495f01ce314",
    "go.sum": "003f522dfaa033358233cc26d4f58d8aee921be0577752eeb94ed8f768cc7d7c",
}
PATCHED_SHA256 = {
    "go.mod": "fa59dc695b21450352fc836b3893f33aa98136e99c82c4362b3f181a46646bf2",
    "go.sum": "59aa58cea4c817e8702d0405dedc9f976fc79c0cf8a9e906d68347089cfc58b8",
}
REPLACEMENTS = {
    "go.mod": (
        "golang.org/x/text v0.14.0 // indirect",
        "golang.org/x/text v0.39.0 // indirect",
    ),
    "go.sum": (
        "golang.org/x/text v0.14.0 h1:ScX5w1eTa3QqT8oi6+ziP7dTV1S2+ALU0bI+0zXKWiQ=\n"
        "golang.org/x/text v0.14.0/go.mod h1:18ZOQIKpY8NJVqYksKHtTdi31H5itFRjB5/qKTNYzSU=",
        "golang.org/x/text v0.39.0 h1:UbZz4pLOvn600D6Oh6GGEI6VAmndrEBLv8/6BEXzyus=\n"
        "golang.org/x/text v0.39.0/go.mod h1:3UwRclnC2g0TU9x8PZiyfOajCd1zaUNHF9cvqcQZ+ZM=",
    ),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_launcher.py <task-runner-launcher-source>")
    root = pathlib.Path(sys.argv[1]).resolve()
    for name, (old, new) in REPLACEMENTS.items():
        path = root / name
        source = path.read_bytes()
        if digest(source) != SOURCE_SHA256[name]:
            raise SystemExit(f"refusing unexpected upstream {name}")
        text = source.decode("utf-8")
        if text.count(old) != 1 or new in text:
            raise SystemExit(f"refusing ambiguous {name} security update")
        patched = text.replace(old, new).encode("utf-8")
        if digest(patched) != PATCHED_SHA256[name]:
            raise SystemExit(f"patched {name} digest mismatch")
        path.write_bytes(patched)


if __name__ == "__main__":
    main()
