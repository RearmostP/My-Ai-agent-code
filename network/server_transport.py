"""FastAPI/Uvicorn transport wrapper with a network-interface extension hook."""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI


def configure_network_interface() -> None:
    """Extension hook for future Tailscale IP discovery, sockets, or proxy setup."""


def get_bind_address() -> tuple[str, int]:
    """Return configured bind host and port; interface routing belongs in this layer."""
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    try:
        port = int(os.getenv("SERVER_PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("SERVER_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("SERVER_PORT must be between 1 and 65535.")
    return host, port


def run(app: FastAPI | None = None) -> None:
    configure_network_interface()
    if app is None:
        from main_app import app as application
    else:
        application = app
    host, port = get_bind_address()
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "WARNING: The agent is listening beyond localhost. Use only on a trusted "
            "network; filesystem and Git operations are exposed without authentication."
        )
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    print(f"Open the agent in your browser at: http://{browser_host}:{port}/")
    if host in ("0.0.0.0", "::", ""):
        print("For another device, use this computer's LAN IP address instead.")
    uvicorn.run(application, host=host, port=port, log_level="info")
