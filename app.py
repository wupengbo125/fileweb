#!/usr/bin/env python3
"""one-files —— 手机优先的极简文件查看/编辑器（零依赖，仅标准库）"""
import hashlib
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.environ.get("ONE_FILES_ROOT", str(Path.home()))).resolve()
PASSWORD = os.environ.get("ONE_FILES_PASSWORD") or os.environ.get("common_password") or ""
MAX_EDIT_SIZE = 2 * 1024 * 1024
TOKEN = hashlib.sha256(PASSWORD.encode()).hexdigest()
COOKIE = "of_session"
HERE = Path(__file__).resolve().parent


def safe_path(rel):
    p = (ROOT / (rel or "")).resolve()
    if not (p == ROOT or p.is_relative_to(ROOT)):
        raise PermissionError("路径越界")
    return p


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.0f}T"


def login_page():
    return """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>one-files</title><style>
body{font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#111;color:#eee}
form{width:80%;max-width:320px}
input{width:100%;padding:14px;font-size:17px;margin:8px 0;border-radius:10px;border:1px solid #444;background:#222;color:#eee;box-sizing:border-box}
button{width:100%;padding:14px;font-size:17px;border:0;border-radius:10px;background:#4a9eff;color:#fff}
</style><form method=post><h2>one-files</h2><input type=password name=password placeholder=密码 autofocus><button>进入</button></form>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _authed(self):
        c = self.headers.get("Cookie", "")
        return any(part.strip() == f"{COOKIE}={TOKEN}" for part in c.split(";"))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/login":
            return self._send(200, login_page())
        if not self._authed():
            return self._send(302, "", "text/plain", [("Location", "/login")])

        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_text(encoding="utf-8"))
        if u.path == "/api/list":
            return self.api_list(q.get("path", [""])[0])
        if u.path == "/api/file":
            return self.api_read(q.get("path", [""])[0])
        self._send(404, "not found", "text/plain")

    def api_list(self, rel):
        try:
            p = safe_path(rel)
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        if not p.is_dir():
            return self._json({"error": "不是目录"}, 400)
        dirs, files = [], []
        for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            try:
                st = e.stat()
            except OSError:
                continue
            item = {
                "name": e.name,
                "type": "dir" if e.is_dir() else "file",
                "size": "" if e.is_dir() else human(st.st_size),
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            }
            (dirs if e.is_dir() else files).append(item)
        return self._json({"path": str(p.relative_to(ROOT)), "parent": str(p.parent.relative_to(ROOT)) if p != ROOT else None, "entries": dirs + files})

    def api_read(self, rel):
        try:
            p = safe_path(rel)
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        if not p.is_file():
            return self._json({"error": "不是文件"}, 400)
        if p.stat().st_size > MAX_EDIT_SIZE:
            return self._json({"error": f"文件超过 {human(MAX_EDIT_SIZE)}，不支持在线编辑"}, 400)
        raw = p.read_bytes()
        if b"\x00" in raw:
            return self._json({"error": "二进制文件，无法编辑"}, 400)
        return self._json({"path": rel, "content": raw.decode("utf-8", errors="replace")})

    # ---------- POST ----------
    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/login":
            body = self._body().decode()
            pwd = parse_qs(body).get("password", [""])[0]
            if pwd == PASSWORD:
                return self._send(302, "", "text/plain", [("Location", "/"),
                                                          ("Set-Cookie", f"{COOKIE}={TOKEN}; Path=/; HttpOnly; Max-Age=2592000")])
            return self._send(200, login_page().replace("<h2>one-files</h2>", "<h2>one-files</h2><p style=color:#ff6b6b>密码错误</p>"))
        if not self._authed():
            return self._send(302, "", "text/plain", [("Location", "/login")])
        if u.path == "/api/file":
            try:
                data = json.loads(self._body().decode())
                p = safe_path(data.get("path", ""))
            except (PermissionError, ValueError) as e:
                return self._json({"error": f"请求无效: {e}"}, 400)
            if not p.is_file():
                return self._json({"error": "不是文件"}, 400)
            p.write_text(data.get("content", ""), encoding="utf-8")
            return self._json({"ok": True})
        self._send(404, "not found", "text/plain")


def main():
    port = int(os.environ.get("ONE_FILES_PORT", "8766"))
    if not PASSWORD:
        sys.exit("未设置密码：需要 ONE_FILES_PASSWORD 或 common_password 环境变量")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
