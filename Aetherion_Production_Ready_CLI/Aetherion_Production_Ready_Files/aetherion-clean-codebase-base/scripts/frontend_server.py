"""
scripts/frontend_server.py — Production-Fidelity Frontend Static Server & API Reverse Proxy
========================================================================================
Serves the compiled React 19 SPA from frontend-auth/dist on port 3000 and proxies
all /api/ requests to the backend at http://127.0.0.1:5000, faithfully matching
deploy/nginx/nginx.conf and vite.config.js.
"""
from __future__ import annotations

import http.server
import mimetypes
import os
import socketserver
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("FRONTEND_PORT", "3000"))
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:5000").rstrip("/")
DIST_DIR = (Path(__file__).resolve().parent.parent / "frontend-auth" / "dist").resolve()

# Ensure proper JS/CSS mimetypes
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("text/html", ".html")


class FrontendAndProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Concise logging
        print(f"[Frontend:3000] {self.command} {self.path} -> {args[0] if args else ''}")

    def do_OPTIONS(self):
        if self.path.startswith("/api/"):
            self._proxy_request()
        else:
            self.send_response(200)
            self._send_security_headers()
            self.end_headers()

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(b"healthy\n")
            return

        if self.path.startswith("/api/"):
            self._proxy_request()
            return

        self._serve_static_or_spa()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy_request()
        else:
            self.send_error(405, "Method Not Allowed")

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._proxy_request()
        else:
            self.send_error(405, "Method Not Allowed")

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy_request()
        else:
            self.send_error(405, "Method Not Allowed")

    def _send_security_headers(self):
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")

    def _serve_static_or_spa(self):
        # Parse path
        parsed = urllib.parse.urlparse(self.path)
        rel_path = parsed.path.lstrip("/")

        target_file = DIST_DIR / rel_path
        if rel_path and target_file.is_file():
            self._serve_file(target_file)
        else:
            # SPA fallback: serve index.html
            index_file = DIST_DIR / "index.html"
            if index_file.is_file():
                self._serve_file(index_file)
            else:
                self.send_error(404, "Frontend dist not found")

    def _serve_file(self, file_path: Path):
        try:
            content = file_path.read_bytes()
            mime, _ = mimetypes.guess_type(str(file_path))
            mime = mime or "application/octet-stream"

            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def _proxy_request(self):
        target_url = f"{BACKEND_URL}{self.path}"
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        headers = {}
        for key, val in self.headers.items():
            if key.lower() not in ("host", "content-length"):
                headers[key] = val

        req = urllib.request.Request(
            target_url,
            data=body,
            headers=headers,
            method=self.command,
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for hkey, hval in resp.headers.items():
                    if hkey.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                        self.send_header(hkey, hval)
                self.send_header("Content-Length", str(len(resp_body)))
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            for hkey, hval in e.headers.items():
                if hkey.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                    self.send_header(hkey, hval)
            self.send_header("Content-Length", str(len(err_body)))
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            self.send_error(502, f"Bad Gateway: {e}")


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"Starting Aetherion Frontend Server on http://127.0.0.1:{PORT} -> proxying to {BACKEND_URL}")
    with ReusableTCPServer(("127.0.0.1", PORT), FrontendAndProxyHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down frontend server.")
            httpd.shutdown()
