#!/usr/bin/env python3
"""fileweb —— 手机优先的极简文件查看/编辑器（零依赖，仅标准库）"""
import cgi
import hashlib
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.environ.get("ONE_FILES_ROOT", str(Path.home() / "onespace" / "github"))).resolve()
PASSWORD = os.environ.get("ONE_FILES_PASSWORD") or os.environ.get("common_password") or ""
MAX_EDIT_SIZE = 2 * 1024 * 1024
MAX_UPLOAD_SIZE = 200 * 1024 * 1024
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


def unique_path(dest):
    """重名不覆盖：name.ext → name 1.ext → name 2.ext ..."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 1
    while (dest.with_name(f"{stem} {i}{suffix}")).exists():
        i += 1
    return dest.with_name(f"{stem} {i}{suffix}")


def login_page(err=""):
    return """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>fileweb</title><style>
:root{--primary:#0066cc;--ink:#1d1d1f;--muted:#7a7a7a;--parchment:#f5f5f7}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:system-ui,-apple-system,"SF Pro Text",sans-serif;font-size:17px;letter-spacing:-.374px;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:var(--parchment);color:var(--ink)}
.card{width:82%;max-width:340px;text-align:center}
h1{font-size:34px;font-weight:600;letter-spacing:-.374px;margin:0 0 6px}
p{font-size:14px;letter-spacing:-.224px;color:var(--muted);margin:0 0 28px}
input{width:100%;padding:12px 20px;height:48px;font-size:17px;margin:0 0 14px;border-radius:9999px;
border:1px solid rgba(0,0,0,.08);background:#fff;color:var(--ink)}
button{width:100%;padding:11px 22px;font-size:17px;border:0;border-radius:9999px;background:var(--primary);color:#fff}
button:active{transform:scale(.95)}
.err{color:#ff3b30;font-size:14px;letter-spacing:-.224px;margin:0 0 14px}
</style><div class=card><h1>fileweb</h1><p>在手机上查看并修改这台机器的文件</p>""" + \
        (f'<p class=err>{err}</p>' if err else "") + \
        '<form method=post><input type=password name=password placeholder=密码 autofocus><button>进入</button></form></div>'


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
            return self._send(200, login_page("密码错误"))
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
        if u.path == "/api/upload":
            return self.api_upload()
        self._send(404, "not found", "text/plain")

    def api_upload(self):
        try:
            fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                                  environ={"REQUEST_METHOD": "POST"})
            p = safe_path(fs.getvalue("path", ""))
            if not p.is_dir():
                return self._json({"error": "目标不是目录"}, 400)
            if "file" not in fs or not fs["file"].filename:
                return self._json({"error": "没有文件"}, 400)
            # 文件名只取 basename，防路径穿越
            name = os.path.basename(fs["file"].filename.replace("\\", "/"))
            if name in ("", ".", ".."):
                return self._json({"error": "文件名无效"}, 400)
            dest = unique_path(p / name)
            size = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = fs["file"].file.read(1024 * 256)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_SIZE:
                        f.close()
                        dest.unlink()
                        return self._json({"error": f"超过 {human(MAX_UPLOAD_SIZE)} 上限"}, 400)
                    f.write(chunk)
            return self._json({"ok": True, "name": dest.name})
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except Exception as e:
            return self._json({"error": f"上传失败: {e}"}, 400)


def main():
    port = int(os.environ.get("ONE_FILES_PORT", "8766"))
    if not PASSWORD:
        sys.exit("未设置密码：需要 ONE_FILES_PASSWORD 或 common_password 环境变量")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
