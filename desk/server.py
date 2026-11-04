"""Local web server: serves the desk page and the small API the browser shim talks to."""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import ROOT, claude_jobs
from .scheduler import TZ as SCHED_TZ
from .sheets import Sheets

COOKIE = "sepa_session"
SESSION_DAYS = 30
JOBS = ("update", "rsrank", "daily", "news", "backup")
LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SEPA Desk</title><style>body{font:16px system-ui;background:#0f1115;color:#e6e6e6;display:grid;place-items:center;height:100vh;margin:0}
form{display:grid;gap:12px;width:min(320px,90vw)}input,button{font:inherit;padding:10px;border-radius:8px;border:1px solid #333;background:#1a1d23;color:inherit}
button{background:#2f6fed;border:0;cursor:pointer}p{color:#e66;margin:0}</style></head><body><form method="post" action="/login">
<b>SEPA + CAN SLIM Desk</b>{err}<input type="password" name="token" placeholder="Access token" autofocus autocomplete="current-password">
<button>Sign in</button></form></body></html>"""


def _is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class Auth:
    """Single-owner auth. Enabled when SEPA_TOKEN is set (required for any non-loopback bind).
    Browser: /login exchanges the token for an HMAC-signed, HttpOnly, SameSite=Strict cookie.
    Scripts: Authorization: Bearer <token>."""

    def __init__(self, token):
        self.token = token or ""
        self.key = hashlib.sha256(("sepa-desk|" + self.token).encode()).digest()

    @property
    def enabled(self):
        return bool(self.token)

    def check_token(self, t):
        return self.enabled and hmac.compare_digest(str(t or "").encode(), self.token.encode())

    def make_cookie(self):
        exp = str(int(time.time()) + SESSION_DAYS * 86400)
        nonce = secrets.token_hex(8)
        sig = hmac.new(self.key, f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{nonce}.{sig}"

    def check_cookie(self, v):
        try:
            exp, nonce, sig = (v or "").split(".")
            good = hmac.new(self.key, f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(sig, good) and int(exp) > time.time()
        except ValueError:
            return False

STATIC = os.path.join(ROOT, "static")

# UI wording that only made sense on claude.ai. Applied when serving, so static/index.html
# stays identical to the published artifact.
LOCAL_COPY = [
    ("A Google Sheet in your Drive runs GOOGLEFINANCE for every watchlist and position ticker.",
     "The local server downloads daily prices from Yahoo Finance for every watchlist and position ticker."),
    ("Update finance data from Google Finance", "Update finance data"),
    ("Price feed from Google Finance", "Price feed (Yahoo Finance, local)"),
    ("Google Finance", "Yahoo Finance"),
    ("in your Google Drive", "on the local server"),
    ("Update the market check: both indexes vs the 50-day and 200-day, distribution days, follow-through day",
     "Check the market regime (distribution days and follow-through are computed from index data each update)"),
]


def make_handler(store, market, auth=None, scheduler=None):
    sheets = Sheets(store, market)
    auth = auth or Auth("")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=STATIC, **kw)

        def log_message(self, fmt, *args):
            if os.environ.get("SEPA_VERBOSE"):
                super().log_message(fmt, *args)

        # ---- helpers
        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _q(self):
            u = urlparse(self.path)
            return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        # ---- auth
        def _cookie(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v
            return ""

        def _authed(self):
            if not auth.enabled:
                return True
            h = self.headers.get("Authorization") or ""
            if h.startswith("Bearer ") and auth.check_token(h[7:]):
                return True
            return auth.check_cookie(self._cookie())

        def _same_origin(self):
            """Writes must come from this page: blocks CSRF from other sites open in the same browser."""
            if (self.headers.get("Authorization") or "").startswith("Bearer "):
                return True
            origin = self.headers.get("Origin")
            if origin:
                return urlparse(origin).netloc == (self.headers.get("X-Forwarded-Host") or self.headers.get("Host"))
            return (self.headers.get("Sec-Fetch-Site") or "same-origin") in ("same-origin", "none")

        def _deny(self, path):
            if path.startswith("/api/"):
                return self._json({"code": "unauthorized", "message": "sign in first"}, 401)
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _login_page(self, err=""):
            body = LOGIN_HTML.replace("{err}", f"<p>{err}</p>" if err else "").encode()
            self.send_response(401 if err else 200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _login(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "replace")) if n else {}
            if not auth.check_token((form.get("token") or [""])[0]):
                time.sleep(1)  # slows brute force
                return self._login_page("Wrong token.")
            secure = (self.headers.get("X-Forwarded-Proto") == "https")
            self.send_response(303)
            self.send_header("Set-Cookie", f"{COOKIE}={auth.make_cookie()}; Path=/; HttpOnly; SameSite=Strict; "
                                           f"Max-Age={SESSION_DAYS * 86400}" + ("; Secure" if secure else ""))
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---- routes
        def do_GET(self):
            path, q = self._q()
            if path == "/api/health":
                return self._json({"ok": True, "seq": store.seq(), "auth": auth.enabled})
            if path == "/login":
                return self._login_page()
            if not self._authed():
                return self._deny(path)
            try:
                if path == "/api/jobs":
                    return self._json({"jobs": (store.get("app/jobs") or {}).get("data", {}),
                                       "schedule": scheduler.config() if scheduler else None,
                                       "tz": str(SCHED_TZ)})
                if path in ("/", "/index.html"):
                    return self._index()
                if path == "/api/seq":
                    return self._json({"seq": store.seq()})
                if path == "/api/doc":
                    d = store.get(q["path"])
                    return self._json({"exists": bool(d), "data": d["data"] if d else None, "version": d["version"] if d else None})
                if path == "/api/col":
                    return self._json({"docs": store.list(q["path"])})
            except Exception as e:
                return self._json({"code": "bad_request", "message": str(e)}, 400)
            return super().do_GET()

        def do_POST(self):
            path, q = self._q()
            if path == "/login":
                return self._login()
            if not self._authed():
                return self._deny(path)
            if not self._same_origin():
                return self._json({"code": "forbidden", "message": "cross-origin write blocked"}, 403)
            try:
                if path == "/logout":
                    self.send_response(303)
                    self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0")
                    self.send_header("Location", "/login")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/api/job":
                    name = q.get("name")
                    if name not in JOBS or not scheduler:
                        return self._json({"code": "bad_request", "message": f"job must be one of {JOBS}"}, 400)
                    return self._json(scheduler.run_job(name))
                body = self._body()
                if path == "/api/doc":
                    op = body.get("op")
                    if op == "set":
                        v = store.set(q["path"], body.get("data") or {})
                    elif op == "update":
                        v = store.update(q["path"], body.get("data") or {})
                    elif op == "delete":
                        store.delete(q["path"]); v = None
                    else:
                        return self._json({"code": "bad_request", "message": "unknown op"}, 400)
                    return self._json({"ok": True, "version": v})
                if path == "/api/mcp":
                    return self._mcp(body)
            except Exception as e:
                return self._json({"code": "tool_error", "message": str(e)}, 500)
            return self._json({"code": "bad_request", "message": "not found"}, 404)

        def _mcp(self, body):
            server, tool, inp = body.get("server"), body.get("tool"), body.get("input") or {}
            try:
                if server == "Google Drive" and tool in ("create_file", "read_file_content", "download_file_content", "trash_file"):
                    return self._json({"payload": getattr(sheets, tool)(inp)})
                if server == "Claude Code Remote" and tool == "fire_trigger":
                    if scheduler:
                        return self._json({"payload": scheduler.run_job("news")})
                    return self._json({"payload": claude_jobs.start_news(store)})
            except Exception as e:
                return self._json({"code": "tool_error", "message": str(e)}, 500)
            return self._json({"code": "not_in_manifest", "message": f"{server}/{tool} isn't available locally"}, 400)

        def _index(self):
            with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
                html = fh.read()
            for a, b in LOCAL_COPY:
                html = html.replace(a, b)
            html = re.sub(r"<head>", '<head>\n<script src="/claude-shim.js"></script>', html, count=1)
            html = html.replace("</body>", '<script src="/local-panel.js"></script>\n</body>', 1)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(store, market, host="127.0.0.1", port=8765, schedule=True):
    token = os.environ.get("SEPA_TOKEN", "")
    if not _is_loopback(host) and not token:
        raise SystemExit(f"Refusing to listen on {host} without SEPA_TOKEN. Set a token (see README, 'Remote access'), "
                         "or keep the default 127.0.0.1 and use `tailscale serve`.")
    if token and len(token) < 20:
        raise SystemExit("SEPA_TOKEN is too short; use at least 20 random characters (python -c \"import secrets;print(secrets.token_urlsafe(24))\").")
    sched = None
    if schedule:
        from .scheduler import Scheduler
        sched = Scheduler(store, market)
        sched.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(store, market, Auth(token), sched))
    httpd.daemon_threads = True
    print(f"SEPA + CAN SLIM Desk running at http://{host}:{port}  auth={'on' if token else 'off (local only)'}  "
          f"scheduler={'on' if sched else 'off'}  (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if sched:
            sched.stop()
