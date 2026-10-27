#!/usr/bin/env python3
"""Start the FastAPI sidecar and announce its port to Tauri."""
from __future__ import annotations

import json
import os
import socket

import uvicorn

from app.main import app


def find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def main() -> None:
    port = int(os.environ.get("KB_PORT", "0") or "0") or find_free_port()
    print(f"KB_READY {json.dumps({'port': port})}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
