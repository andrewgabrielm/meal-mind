#!/usr/bin/env python3
"""Dev server for the MealMind PWA.

Binds all interfaces (so your phone can reach it over the LAN), serves the
correct MIME types for the manifest and service worker, and prints every URL
a phone on the same Wi-Fi could use. Not for production — it is a static
file server with no TLS.

Run:  cd frontend && python3 serve.py       # http://localhost:8080
"""
import http.server
import os
import signal
import socket
import socketserver
import subprocess
import time
from pathlib import Path

PORT = 8080
ROOT = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".webmanifest": "application/manifest+json",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".json": "application/json",
        ".svg": "image/svg+xml",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # the service worker must be revalidated, or stale shells stick around
        if self.path.endswith(("sw.js", "index.html", "/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def lan_addresses() -> list[str]:
    addrs = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addrs.add(info[4][0])
    except socket.gaierror:
        pass
    # UDP trick: no packets sent, but the OS picks the outbound interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True     # must be set before bind, or restarts hit
    daemon_threads = True          # "Address already in use" for ~a minute


def free_port() -> None:
    """Replace an instance already on PORT — including one suspended with
    Ctrl+Z, which holds the socket but ignores a plain terminate."""
    try:
        pids = subprocess.run(["lsof", "-t", f"-iTCP:{PORT}", "-sTCP:LISTEN"],
                              capture_output=True, text=True).stdout.split()
    except FileNotFoundError:
        return
    mine = str(os.getpid())
    for pid in (p for p in pids if p != mine):
        for sig in (signal.SIGCONT, signal.SIGTERM):
            try:
                os.kill(int(pid), sig)
            except (ProcessLookupError, ValueError, PermissionError):
                pass
        time.sleep(0.6)
        try:
            os.kill(int(pid), 0)          # still alive? insist
            os.kill(int(pid), signal.SIGKILL)
            time.sleep(0.4)
        except (ProcessLookupError, ValueError, PermissionError):
            pass
        print(f"(replaced the server already on port {PORT})", flush=True)


def main() -> None:
    free_port()
    with Server(("", PORT), Handler) as httpd:
        print(f"MealMind PWA on:\n  http://localhost:{PORT}")
        for a in lan_addresses():
            print(f"  http://{a}:{PORT}   <- use this one on your phone")
        print("Backend expected on port 8000 of the same host "
              "(uvicorn app.main:app --host 0.0.0.0).")
        print("Phone can't load it? See DEBUG_PHONE.md. Ctrl+C stops.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye")


if __name__ == "__main__":
    main()
