"""On-demand loopback comparison UI; only scoped assets and typed mutations."""

from __future__ import annotations

import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .audition import AuditionService
from .contracts import canonical, parse_json
from .delivery import ExportService
from .errors import AudioError


def create_server(store, audition_id, registry=None, *, port=0):
    service = AuditionService(store, registry)
    spec = service.show(audition_id)
    session_id = spec["request"]["session_id"]
    token = secrets.token_urlsafe(32)
    web = Path(__file__).with_name("web")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, content_type="application/json; charset=utf-8", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def authorized(self, api=True):
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host or self.headers.get("Origin", "http://" + host) != "http://" + host:
                raise AudioError("access_denied", "Unexpected request origin")
            if api and not secrets.compare_digest(self.headers.get("X-Matter-Token", "").encode("utf-8"), token.encode("ascii")):
                raise AudioError("access_denied", "Open the comparison with its local access link")

        def ids(self):
            ids = set(spec["assets"])
            current = service.sessions.show(session_id)["current"]["selected_asset"]
            if current:
                ids.add(current["asset_id"])
            return ids

        def scoped_audio(self, asset_id):
            if asset_id not in self.ids():
                raise AudioError("access_denied", "Asset is outside this comparison")
            return store.asset(asset_id)[1]

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                static = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/style.css": ("style.css", "text/css; charset=utf-8")}
                self.authorized(api=path not in static)
                if path in static:
                    name, mime = static[path]
                    self.reply(200, (web / name).read_bytes(), mime)
                elif path == "/api/state":
                    self.reply(200, canonical(service.state(audition_id)))
                elif path.startswith("/api/audio/"):
                    data = self.scoped_audio(path.removeprefix("/api/audio/"))
                    size, status, headers = len(data), 200, {"Accept-Ranges": "bytes"}
                    if self.headers.get("Range"):
                        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers["Range"])
                        if not match or not any(match.groups()):
                            self.reply(416, b"", extra={"Content-Range": f"bytes */{size}"})
                            return
                        first, last = match.groups()
                        start = int(first) if first else max(0, size - int(last))
                        end = min(size - 1, int(last)) if first and last else size - 1
                        if not 0 <= start <= end < size:
                            self.reply(416, b"", extra={"Content-Range": f"bytes */{size}"})
                            return
                        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                        data, status = data[start:end + 1], 206
                    self.reply(status, data, "audio/wav", headers)
                elif path.startswith("/api/export/"):
                    exported = ExportService(store).show(path.removeprefix("/api/export/"))
                    if exported["request"]["session_id"] != session_id:
                        raise AudioError("access_denied", "Export belongs to another session")
                    self.reply(200, Path(exported["path"]).read_bytes(), "audio/wav",
                               {"Content-Disposition": 'attachment; filename="selected.wav"'})
                else:
                    self.reply(404, canonical({"error": {"code": "not_found"}}))
            except (AudioError, OSError, ValueError) as exc:
                self.failure(exc)

        def do_POST(self):
            try:
                self.authorized()
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024 or self.headers.get_content_type() != "application/json":
                    raise AudioError("invalid_request", "Expected a bounded JSON body")
                body = parse_json(self.rfile.read(length))
                if not isinstance(body, dict) or body.get("session_id") != session_id:
                    raise AudioError("access_denied", "Mutation belongs to another session")
                path = urlsplit(self.path).path
                if path == "/api/select":
                    if "asset_id" in body and body["asset_id"] not in self.ids():
                        raise AudioError("access_denied", "Candidate is outside this comparison")
                    result = service.sessions.mutate("select", body)
                elif path == "/api/feedback":
                    result = service.sessions.mutate("feedback", body)
                elif path == "/api/export":
                    result = ExportService(store).create(body)
                else:
                    raise AudioError("unsupported_command", "Unknown comparison mutation")
                self.reply(200, canonical(result))
            except (AudioError, OSError, ValueError) as exc:
                self.failure(exc)

        def failure(self, exc):
            error = exc.document() if isinstance(exc, AudioError) else {"code": "io_error", "message": str(exc)}
            status = 403 if error["code"] == "access_denied" else 409 if "conflict" in error["code"] else 400
            self.reply(status, canonical({"status": "failed", "error": error}))

    if type(port) is not int or not 0 <= port <= 65535:
        raise AudioError("invalid_request", "Port must be 0..65535")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.matter_token = token
    server.matter_url = f"http://127.0.0.1:{server.server_port}/#" + token
    return server


def serve(store, audition_id, registry=None, *, port=0, ready_file=None):
    server = create_server(store, audition_id, registry, port=port)
    info = {"status": "ready", "url": server.matter_url, "audition_id": audition_id}
    try:
        if ready_file:
            from .artifacts import safe_path
            path = safe_path(ready_file)
            with path.open("x", encoding="utf-8") as stream:
                json.dump(info, stream)
        print(json.dumps(info), flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {"status": "stopped"}
