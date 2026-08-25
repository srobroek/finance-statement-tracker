from __future__ import annotations

import socket

try:
    socket.create_connection(("1.1.1.1", 53), timeout=1).close()
except OSError:
    raise SystemExit(0)
raise SystemExit("network was unexpectedly reachable")
