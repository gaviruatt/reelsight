"""Tiny localhost server for the ReelSight live viewer.

Serves viewer.html at /v/<job> and the job files (state.json, frames, video)
from the cache folder, with Range support so the video can seek. Binds to
127.0.0.1 only and exits after 30 idle minutes.
"""
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT, PORT = Path(sys.argv[1]), int(sys.argv[2])
PAGE = (Path(__file__).parent / "viewer.html").read_bytes()
IDLE = 30 * 60
last = time.time()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        global last
        last = time.time()
        if self.path == "/ping":
            return self._send(200, b"ok", "text/plain")
        if self.path.startswith("/v/"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        rng = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not rng or not path.is_file():
            return super().do_GET()
        size = path.stat().st_size
        start, _, end = rng.replace("bytes=", "").partition("-")
        start = int(start or 0)
        end = min(int(end) if end else size - 1, size - 1)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0 and (chunk := f.read(min(65536, left))):
                self.wfile.write(chunk)
                left -= len(chunk)

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def reaper(srv):
    while time.time() - last < IDLE:
        time.sleep(30)
    srv.shutdown()


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=reaper, args=(srv,), daemon=True).start()
    try:
        srv.serve_forever()
    except (BrokenPipeError, ConnectionResetError, KeyboardInterrupt):
        pass
    os._exit(0)
