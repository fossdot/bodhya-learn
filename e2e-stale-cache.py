"""Proves the service worker refuses a stale shell served on the UNVERSIONED game URL.

    bench serve --port 8002
    /tmp/pw/bin/python e2e-stale-cache.py

Why this exists. /assets/ is served `cache-control: immutable, max-age=31536000`, and Frappe
Cloud's proxy keeps a SEPARATE entry per Accept-Encoding variant. After the L1-L5 release the
variant modern Chrome asks for ("gzip, deflate, br, zstd") went on serving the previous week's
game.html for hours, while three other variants served the current one -- so a plain curl said
everything was fine and every real learner saw a build with no level feature in it. The PWA's
manifest start_url, every bookmark and every shared link are that same unversioned URL.

This spins up a proxy in front of the dev server that reproduces exactly that: the bare URL
returns a stale marker page, anything with ?r= passes through to the real game. Then it checks
that a browser WITHOUT the service worker really is poisoned (so the test can fail), and that
once the worker is in control the same navigation renders the current build instead -- with the
address bar still on the bare URL, because PWA identity depends on start_url not moving.
"""
import http.server, socketserver, sys, threading, urllib.parse, urllib.request
from playwright.sync_api import sync_playwright

UP, PORT = "http://localhost:8002", 8099
B = "http://localhost:%d" % PORT
BARE = B + "/assets/hikmat/game.html"
STALE = (b"<!DOCTYPE html><html><head><meta charset='utf-8'><title>STALE</title></head>"
         b"<body><h1 id='stalemarker'>STALE BUILD FROM LAST WEEK</h1>"
         b"<script>window.__STALE__ = true;</script></body></html>")


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if parts.path == "/assets/hikmat/game.html" and "r=" not in (parts.query or ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(STALE)))
            self.send_header("Cache-Control", "max-age=31536000, immutable")
            self.end_headers(); self.wfile.write(STALE); return
        try:
            req = urllib.request.Request(UP + self.path, headers={"Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                self.send_response(r.status)
                for k, v in r.headers.items():
                    if k.lower() in ("content-length", "transfer-encoding", "content-encoding", "connection"):
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
        except Exception:
            self.send_response(502); self.send_header("Content-Length", "0"); self.end_headers()


fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

def state(page):
    return page.evaluate("({stale: !!window.__STALE__, marker: !!document.getElementById('stalemarker'),"
                         " assetURL: typeof assetURL, pill: document.querySelectorAll('#levelPill').length})")


socketserver.ThreadingTCPServer.allow_reuse_address = True
srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print("stale-proxy on", PORT)

try:
    with sync_playwright() as p:
        b = p.chromium.launch()

        # A. no service worker yet -- the bare URL is the bug, undefended. If this passes as
        #    "not stale" the rest of the test proves nothing, so it is checked explicitly.
        ctx = b.new_context(viewport={"width": 420, "height": 860}); page = ctx.new_page()
        page.goto(BARE, wait_until="domcontentloaded"); page.wait_for_timeout(1200)
        st = state(page); print("  first-ever visit:", st)
        check(st["stale"] is True, "BASELINE: with no SW the bare URL serves the stale build (test is meaningful)")
        ctx.close()

        # B. register through /play, then come back to the bare URL
        ctx = b.new_context(viewport={"width": 420, "height": 860}); page = ctx.new_page()
        page.goto(B + "/play", wait_until="domcontentloaded")
        page.wait_for_function("typeof COURSES !== 'undefined' && COURSES.length > 5", timeout=30000)
        page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller", timeout=30000)
        page.wait_for_timeout(2000)
        page.goto(BARE, wait_until="domcontentloaded"); page.wait_for_timeout(3000)
        st = state(page); print("  bare URL with the SW active:", st, "| url:", page.url)
        check(st["stale"] is False and st["marker"] is False, "the SW refuses the stale bare document")
        check(st["assetURL"] == "function" and st["pill"] == 1, "bare navigation renders the CURRENT build")
        check(page.url == BARE, "address bar stays on the bare start_url (PWA identity intact)")

        # C. and none of that may cost us offline
        ctx.set_offline(True)
        page.goto(BARE, wait_until="domcontentloaded"); page.wait_for_timeout(3000)
        n = page.evaluate("typeof COURSES !== 'undefined' ? COURSES.length : -1")
        st = state(page); print("  offline bare navigation:", st, "COURSES:", n)
        check(n > 0 and st["stale"] is False, "offline bare navigation opens the CURRENT cached shell")
        ctx.set_offline(False)
        ctx.close(); b.close()
finally:
    srv.shutdown()

print("\nFAILS", len(fails), fails)
sys.exit(1 if fails else 0)
