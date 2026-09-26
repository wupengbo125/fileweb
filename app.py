#!/usr/bin/env python3
"""fileweb —— 手机优先的极简文件查看/编辑器（零依赖，仅标准库）"""
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

ROOT = Path(os.environ.get("ONE_FILES_ROOT", str(Path.home() / "onespace" / "github"))).resolve()
PASSWORD = os.environ.get("ONE_FILES_PASSWORD") or os.environ.get("common_password") or ""
MAX_EDIT_SIZE = 2 * 1024 * 1024
MAX_UPLOAD_SIZE = 200 * 1024 * 1024
TOKEN = hashlib.sha256(PASSWORD.encode()).hexdigest()
COOKIE = "of_session"
HERE = Path(__file__).resolve().parent
# 文件写与同步共用一把锁，防并发互踩（个人单机足够）
_LOCK = threading.Lock()


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


def git_repo_root(p):
    """从 p 向上找最近的 .git，不越出 ROOT"""
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
        if d == ROOT:
            break
    return None


def sync_repo(root):
    """pull → add -A → commit（信息 debug，无改动则跳过）→ push；返回 (ok, 错误信息, log)"""
    def git(*args):
        r = subprocess.run(("git",) + args, cwd=str(root), capture_output=True,
                           text=True, timeout=300)
        return r.returncode, (r.stdout + r.stderr).strip()

    log = []
    try:
        for args in (("pull",), ("add", "-A")):
            code, out = git(*args)
            log.append(f"git {' '.join(args)} → {out.splitlines()[0] if out else 'ok'}")
            if code:
                return False, out or f"git {args[0]} 失败", log
        if git("diff", "--cached", "--quiet")[0]:   # 有暂存改动才提交
            code, out = git("commit", "--no-verify", "-m", "debug")
            log.append(f"git commit --no-verify → {out.splitlines()[0] if out else 'ok'}")
            if code:
                return False, out or "git commit 失败", log
        else:
            log.append("git commit → 无改动，跳过")
        code, out = git("push")
        log.append(f"git push → {out.splitlines()[0] if out else 'ok'}")
        if code:
            return False, out or "git push 失败", log
    except subprocess.TimeoutExpired:
        return False, "同步超时", log
    return True, "", log


def login_page(err=""):
    return """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>fileweb</title><link rel=icon type=image/png sizes=32x32 href=/favicon-32.png>
<link rel=manifest href=/manifest.json>
<meta name=theme-color content=#f5f5f7>
<style>
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
        '<form method=post><input type=password name=password placeholder=密码 autofocus><button>进入</button></form></div>' \
        "<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js',{scope:'/'})</script>"


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
        # 图标白名单在认证之前：iOS 抓取主屏幕图标时不带 cookie
        # 图标 / PWA 静态资源白名单在认证之前：Chrome/系统抓取 manifest、SW、图标时不带 cookie
        public = {"/favicon.ico": ("assets/favicon.ico", "image/x-icon"),
                  "/favicon-32.png": ("assets/favicon-32.png", "image/png"),
                  "/favicon-16.png": ("assets/favicon-16.png", "image/png"),
                  "/apple-touch-icon.png": ("assets/apple-touch-icon.png", "image/png"),
                  "/icon-192.png": ("assets/icon-192.png", "image/png"),
                  "/icon-512.png": ("assets/icon-512.png", "image/png"),
                  "/manifest.json": ("manifest.json", "application/manifest+json"),
                  "/sw.js": ("sw.js", "application/javascript")}
        if u.path in public:
            fn, ct = public[u.path]
            return self._send(200, (HERE / fn).read_bytes(), ct,
                              [("Cache-Control", "public, max-age=86400")])
        if not self._authed():
            return self._send(302, "", "text/plain", [("Location", "/login")])

        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_text(encoding="utf-8"))
        if u.path == "/api/list":
            return self.api_list(q.get("path", [""])[0])
        if u.path == "/api/file":
            return self.api_read(q.get("path", [""])[0])
        if u.path.startswith("/raw/"):
            return self.api_raw(unquote(u.path[5:]))
        self._send(404, "not found", "text/plain")

    def api_raw(self, rel):
        try:
            p = safe_path(rel)
        except PermissionError as e:
            return self._send(403, str(e), "text/plain; charset=utf-8")
        if p.is_dir():
            idx = p / "index.html"
            if idx.is_file():
                p = idx
            else:
                return self._send(404, "不是文件", "text/plain; charset=utf-8")
        if not p.is_file():
            return self._send(404, "文件不存在", "text/plain; charset=utf-8")
        ctype, _ = mimetypes.guess_type(p.name)
        if not ctype:
            ctype = "application/octet-stream"
        elif ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        try:
            content = p.read_bytes()
        except OSError as e:
            return self._send(500, f"读取失败: {e}", "text/plain; charset=utf-8")
        return self._send(200, content, ctype)


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
            with _LOCK:
                p.write_text(data.get("content", ""), encoding="utf-8")
            return self._json({"ok": True})
        if u.path == "/api/delete":
            return self.api_delete()
        if u.path == "/api/upload":
            return self.api_upload()
        if u.path == "/api/sync":
            return self.api_sync()
        self._send(404, "not found", "text/plain")

    def api_sync(self):
        """同步当前所在仓库：先 pull，再 commit（信息 debug），最后 push；
        在 GitHub 根目录时并发同步其下所有仓库"""
        try:
            p = safe_path(json.loads(self._body().decode()).get("path", ""))
        except (PermissionError, ValueError) as e:
            return self._json({"error": f"请求无效: {e}"}, 400)
        base = p if p.is_dir() else p.parent

        with _LOCK:
            if base == ROOT:
                repos = sorted(d for d in ROOT.iterdir() if d.is_dir() and (d / ".git").exists())
                if not repos:
                    return self._json({"error": "根目录下没有 Git 仓库"}, 400)
                with ThreadPoolExecutor(max_workers=min(8, len(repos))) as ex:
                    rs = list(ex.map(sync_repo, repos))
                log = [f"{r.name} → {lg[-1] if lg else 'ok'}" for r, (_, _, lg) in zip(repos, rs)]
                failed = [r.name for r, (ok, _, _) in zip(repos, rs) if not ok]
                if failed:
                    names = "、".join(failed[:3]) + ("…" if len(failed) > 3 else "")
                    return self._json({"error": f"{len(failed)} 个仓库失败：{names}", "log": log}, 400)
                return self._json({"ok": True, "repo": f"{len(repos)} 个仓库", "log": log})

            root = git_repo_root(base)
            if not root:
                return self._json({"error": "当前目录不在 Git 仓库中"}, 400)
            ok, err, log = sync_repo(root)
            if not ok:
                return self._json({"error": err, "log": log}, 400)
            return self._json({"ok": True, "repo": root.name, "log": log})

    def api_delete(self):
        """删除文件或文件夹（目录连内容一起删），不可撤销"""
        try:
            p = safe_path(json.loads(self._body().decode()).get("path", ""))
        except (PermissionError, ValueError) as e:
            return self._json({"error": f"请求无效: {e}"}, 400)
        if p == ROOT:
            return self._json({"error": "不能删除根目录"}, 400)
        if not p.exists():
            return self._json({"error": "文件或文件夹不存在"}, 400)
        with _LOCK:
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError as e:
                return self._json({"error": f"删除失败: {e}"}, 400)
        return self._json({"ok": True})

    def api_upload(self):
        try:
            # email 解析需要消息头，补上 Content-Type 再喂给 BytesParser
            hdr = f"MIME-Version: 1.0\r\nContent-Type: {self.headers.get('Content-Type', '')}\r\n\r\n"
            msg = BytesParser(policy=policy.default).parsebytes(hdr.encode() + self._body())
            if not msg.is_multipart():
                return self._json({"error": "不是上传表单"}, 400)
            fields, fp = {}, None
            for part in msg.iter_parts():
                nm = part.get_param("name", header="content-disposition")
                if nm == "file":
                    fp = part
                elif nm:
                    fields[nm] = part.get_payload(decode=True).decode()
            p = safe_path(fields.get("path", ""))
            if not p.is_dir():
                return self._json({"error": "目标不是目录"}, 400)
            if fp is None or not fp.get_filename():
                return self._json({"error": "没有文件"}, 400)
            # 文件名只取 basename，防路径穿越
            name = os.path.basename(fp.get_filename().replace("\\", "/"))
            if name in ("", ".", ".."):
                return self._json({"error": "文件名无效"}, 400)
            dest = unique_path(p / name)
            data = fp.get_payload(decode=True)
            if len(data) > MAX_UPLOAD_SIZE:
                return self._json({"error": f"超过 {human(MAX_UPLOAD_SIZE)} 上限"}, 400)
            with _LOCK:
                with open(dest, "wb") as f:
                    f.write(data)
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
