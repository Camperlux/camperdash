"""Develop the hub's web pages on the PC, against the real hub's data.

Serves the pages from static/ (as the hub would, same addresses) and passes
/api/ reads through to the hub, so the pages show live data without being
deployed. Anything that would change something - a heater command, a
setting, panic - is refused unless --allow-posts is given, so testing a page
cannot switch the heater on by accident.

    python tools/dev_web.py --hub 192.168.4.187            http://localhost:8766
"""

import argparse
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
ROUTES = {"/": "app.html", "/settings": "settings.html", "/details": "index.html",
          "/heater": "heater.html", "/weather": "weather.html"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript",
         ".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="192.168.4.187")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--allow-posts", action="store_true")
    a = ap.parse_args()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self, method, body=None):
            req = urllib.request.Request("http://%s%s" % (a.hub, self.path), data=body,
                                         method=method,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    self._send(r.status, r.read(), r.headers.get("Content-Type", "application/json"))
            except Exception as e:
                self._send(502, ('{"ok": false, "error": "hub: %s"}' % e).encode(),
                           "application/json")

        def do_GET(self):
            p = self.path.split("?")[0]
            if p.startswith("/api/"):
                return self._proxy("GET")
            fn = ROUTES.get(p, p.lstrip("/"))
            path = os.path.join(STATIC, fn)
            if not os.path.isfile(path):
                return self._send(404, b"not found", "text/plain")
            with open(path, "rb") as f:
                self._send(200, f.read(), TYPES.get(os.path.splitext(fn)[1], "application/octet-stream"))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            if not a.allow_posts:
                print("blocked POST", self.path, body[:120])
                return self._send(200, b'{"ok": false, "error": "blocked by the dev server"}',
                                  "application/json")
            return self._proxy("POST", body)

    print("dev server on http://localhost:%d, data from %s%s"
          % (a.port, a.hub, "" if a.allow_posts else ", changes blocked"), flush=True)
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
