from __future__ import annotations

import socket

client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.settimeout(2)
try:
    client.connect("/run/finance-pdf/pdf.sock")
    client.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
    response = client.recv(256)
finally:
    client.close()
if b" 200 " not in response.split(b"\r\n", 1)[0]:
    raise SystemExit(1)
