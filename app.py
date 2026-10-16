#!/usr/bin/env python3
"""fileweb —— 手机优先的极简文件查看/编辑器（零依赖，仅标准库）"""
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
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
# 项目包同步：暂存仓根目录（提交推送源），真仓取 ROOT/<项目名>（只做 ff-only 拉取）
STAGE_BASE = Path(os.environ.get("ONE_SYNC_BASE", "~/onespace/hsgithub")).expanduser()
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


def check_name(name):
    """新建/重命名共用的取名规则：合法返回空串，否则返回中文错误"""
    if not name or "/" in name or "\\" in name or name in (".", "..") \
            or any(ord(c) < 32 for c in name):      # 拦换行等控制字符
        return "名称无效"
    if len(name.encode()) > 255:
        return "名称过长"
    if name == ".git":                              # 误建会让目录被认成仓库，同步必挂
        return "不能用作 .git"
    return ""


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


def git(root, *args):
    """在 root 里跑 git 子命令，返回 (returncode, stdout+stderr)"""
    r = subprocess.run(("git",) + args, cwd=str(root), capture_output=True,
                       text=True, timeout=300)
    return r.returncode, (r.stdout + r.stderr).strip()


def sync_repo(root):
    """pull → add -A → commit（信息 debug，无改动则跳过）→ push；返回 (ok, 错误信息, log)"""
    log = []
    try:
        for args in (("pull",), ("add", "-A")):
            code, out = git(root, *args)
            log.append(f"git {' '.join(args)} → {out.splitlines()[0] if out else 'ok'}")
            if code:
                return False, out or f"git {args[0]} 失败", log
        if git(root, "diff", "--cached", "--quiet")[0]:   # 有暂存改动才提交
            code, out = git(root, "commit", "--no-verify", "-m", "debug")
            log.append(f"git commit --no-verify → {out.splitlines()[0] if out else 'ok'}")
            if code:
                return False, out or "git commit 失败", log
        else:
            log.append("git commit → 无改动，跳过")
        code, out = git(root, "push")
        log.append(f"git push → {out.splitlines()[0] if out else 'ok'}")
        if code:
            return False, out or "git push 失败", log
    except subprocess.TimeoutExpired:
        return False, "同步超时", log
    return True, "", log


def deploy_zip(name, data):
    """把 <name>.zip 覆盖式解压进暂存仓 STAGE_BASE/<name> → 提交推送 →
    真仓 ROOT/<name> ff-only 拉取；返回 (ok, 错误信息, log)。
    暂存仓不存在时从真仓拷 .git 建出来，之后一直复用"""
    log = []
    stage, real = STAGE_BASE / name, ROOT / name
    try:
        if not (stage / ".git").is_dir():        # 首次：拷真仓的 .git（不拷 1.3G 工作区）
            if not (real / ".git").is_dir():
                return False, f"真仓 {real} 不存在或不是 Git 仓库", log
            shutil.copytree(real / ".git", stage / ".git")
            code, out = git(stage, "reset", "--hard")   # 拷来的仓工作区是空的，按 HEAD 铺出来
            log.append(f"git reset --hard → {out.splitlines()[0] if out else 'ok'}")
            if code:
                return False, out or "git reset 失败", log
        code, out = git(stage, "pull")
        log.append(f"git pull → {out.splitlines()[0] if out else 'ok'}")
        if code:
            return False, out or "git pull 失败", log
        # 先整体校验再动工作区：坏包不能把暂存仓清空
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for item in z.namelist():
                segs = item.split("/")
                if item.startswith("/") or ".." in segs or ".git" in segs:
                    return False, f"压缩包内含非法路径: {item}", log
            for p in stage.iterdir():        # 校验通过才清空工作区，只留 .git
                if p.name == ".git":
                    continue
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            z.extractall(stage)
            for zi in z.infolist():          # extractall 不还原权限位，执行位会丢
                mode = (zi.external_attr >> 16) & 0o777
                if zi.create_system == 3 and mode:
                    os.chmod(stage / zi.filename, mode)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
        # RuntimeError：加密包；NotImplementedError：未知压缩法
        return False, f"不是有效的 zip: {e}", log
    except subprocess.TimeoutExpired:
        return False, "同步超时", log

    items = [p for p in stage.iterdir() if p.name != ".git"]
    if len(items) == 1 and items[0].is_dir():    # 拆平：包内只有一个顶层目录
        top = items[0]
        for p in top.iterdir():
            shutil.move(str(p), str(stage / p.name))
        top.rmdir()

    try:
        code, out = git(stage, "add", "-A")
        log.append(f"git add -A → {out.splitlines()[0] if out else 'ok'}")
        if code:
            return False, out or "git add 失败", log
        if git(stage, "diff", "--cached", "--quiet")[0]:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            code, out = git(stage, "commit", "--no-verify", "-m", f"sync: {name}.zip {stamp}")
            log.append(f"git commit --no-verify → {out.splitlines()[0] if out else 'ok'}")
            if code:
                return False, out or "git commit 失败", log
        else:
            log.append("无改动，跳过")
        code, out = git(stage, "push")
        log.append(f"git push → {out.splitlines()[0] if out else 'ok'}")
        if code:
            return False, out or "git push 失败", log
        code, out = git(real, "pull", "--ff-only")
        log.append(f"git pull --ff-only → {out.splitlines()[0] if out else 'ok'}")
        if code:                                 # push 已成功，真仓拉取失败不回滚
            return False, f"已推送，但 {name} 拉取失败", log
    except subprocess.TimeoutExpired:
        return False, "同步超时", log
    return True, "", log


# ---------- markdown 预览渲染（纯标准库，够用即可，非完整 CommonMark） ----------
# 安全模型：预览页是同源 iframe、带会话 cookie、能调 /api/* 改文件。
# 因此所有文本先转义再拼装，md 里的裸 HTML 一律当文本；链接/图片 URL 走白名单，
# javascript:/data:/vbscript: 等一律降级为纯文本，不生成可点链接。
_RE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_RE_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_RE_HEAD = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_RE_QUOTE = re.compile(r"^ {0,3}>[ \t]?")
_RE_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_RE_TDELIM = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_RE_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_RE_CODE = re.compile(r"`([^`\n]+)`")
_RE_IMG = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')
_RE_LINK = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
_RE_BOLD_S = re.compile(r"\*\*(?!\s)([^*]+?)(?<!\s)\*\*")
_RE_BOLD_U = re.compile(r"(?<!\w)__(?!\s)([^_]+?)(?<!\s)__(?!\w)")
_RE_ITAL_S = re.compile(r"\*(?!\s)([^*]+?)(?<!\s)\*")
_RE_ITAL_U = re.compile(r"(?<!\w)_(?!\s)([^_]+?)(?<!\s)_(?!\w)")
_RE_DEL = re.compile(r"~~(?!\s)([^~]+?)(?<!\s)~~")
_RE_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")

_MD_CSS = """
:root{--ink:#1d1d1f;--muted:#7a7a7a;--primary:#0066cc;--parchment:#f5f5f7;--hairline:#e0e0e0}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#fff;color:var(--ink);font-family:system-ui,-apple-system,"SF Pro Text",sans-serif;font-size:16px;line-height:1.6;letter-spacing:-.2px}
main{max-width:760px;margin:0 auto;padding:20px 16px 64px}
h1,h2,h3,h4,h5,h6{font-family:system-ui,-apple-system,"SF Pro Display",sans-serif;font-weight:600;line-height:1.3;margin:1.3em 0 .55em}
h1{font-size:28px}h2{font-size:23px}h3{font-size:19px}h4,h5,h6{font-size:16px}
main>:first-child{margin-top:0}
p{margin:.65em 0}
a{color:var(--primary);text-decoration:none}
code{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:.92em;background:var(--parchment);border-radius:5px;padding:.1em .36em}
pre{background:var(--parchment);border-radius:10px;padding:12px 14px;overflow:auto;margin:.8em 0}
pre code{background:none;padding:0;font-size:14px;line-height:1.5}
blockquote{margin:.8em 0;padding:.15em 0 .15em 14px;border-left:3px solid var(--hairline);color:var(--muted)}
blockquote p{margin:.3em 0}
hr{border:0;border-top:1px solid var(--hairline);margin:1.6em 0}
ul,ol{margin:.65em 0;padding-left:1.5em}
li{margin:.22em 0}
table{border-collapse:collapse;width:100%;margin:.9em 0;font-size:15px}
th,td{border:1px solid var(--hairline);padding:7px 10px;text-align:left}
th{background:var(--parchment);font-weight:600}
img{max-width:100%;height:auto}
del{color:var(--muted)}
"""


def esc(s):
    """转义 HTML 文本（& < > "），顺手去掉 NUL（行内占位符的分隔符）"""
    return (s.replace("\x00", "")
             .replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def safe_url(url):
    """URL 白名单：http/https/mailto/相对路径/#锚点 放行（返回原串），
    其余（javascript:/data:/vbscript:/协议相对 // 及反斜杠变体）返回 None，调用方降级为纯文本"""
    u = url.strip()
    if not u or any(c.isspace() or ord(c) < 0x20 for c in u):
        return None                    # 含空白/控制字符：浏览器解析 URL 时会剥掉，直接拒
    low = u.lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return u
    if u.startswith("#"):
        return u
    if _RE_SCHEME.match(u) or u.startswith(("//", "\\\\", "/\\", "\\/")):
        return None                    # 协议相对（含反斜杠变体，浏览器会规范成外域 http）
    return u


def _inline(t, stash):
    """行内样式替换；t 必须是已转义文本，stash 为代码段占位符栈（递归调用共享）。
    占位符在中间层不还原——否则链接文本里的 <code> 会被外层粗斜体正则污染，
    统一留到 inline() 所有样式替换做完后单次还原。"""
    def _code(m):
        stash.append("<code>" + m.group(1) + "</code>")
        return f"\x00{len(stash) - 1}\x00"

    t = _RE_CODE.sub(_code, t)         # 代码段优先级最高，先存起来防止被行内规则二次处理

    def _img(m):
        src = safe_url(m.group(2))
        if src is None:
            return m.group(1)          # 降级：只留 alt 文本（已转义）
        return f'<img src="{src}" alt="{m.group(1)}">'

    t = _RE_IMG.sub(_img, t)

    def _link(m):
        url = safe_url(m.group(2))
        if url is None:
            return f"{m.group(1)} ({m.group(2)})"   # 降级纯文本（已转义）
        return f'<a href="{url}">{_inline(m.group(1), stash)}</a>'

    t = _RE_LINK.sub(_link, t)
    t = _RE_BOLD_S.sub(r"<strong>\1</strong>", t)
    t = _RE_BOLD_U.sub(r"<strong>\1</strong>", t)
    t = _RE_ITAL_S.sub(r"<em>\1</em>", t)
    t = _RE_ITAL_U.sub(r"<em>\1</em>", t)
    t = _RE_DEL.sub(r"<del>\1</del>", t)
    return t                            # 占位符不在此处还原


def inline(text):
    stash = []
    t = _inline(esc(text), stash)
    return _RE_PLACEHOLDER.sub(lambda m: stash[int(m.group(1))], t)   # 样式全做完后统一还原


def _dedent(line, n):
    if line.startswith("\t"):
        line = "    " + line[1:]
    i = 0
    while i < n and i < len(line) and line[i] == " ":
        i += 1
    return line[i:]


def _split_row(ln):
    s = ln.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _align(cell):
    l, r = cell.startswith(":"), cell.endswith(":")
    return "center" if l and r else "right" if r else "left" if l else None


def _al_attr(a):
    return f' style="text-align:{a}"' if a else ""


def _is_table_start(lines, i):
    return i + 1 < len(lines) and "|" in lines[i] and bool(_RE_TDELIM.match(lines[i + 1]))


def _paragraph(lines):
    segs = []
    for k, ln in enumerate(lines):
        segs.append(inline(ln))
        if k < len(lines) - 1:
            segs.append("<br>" if re.search(r"[ \t]{2,}$", ln) else "\n")   # 行尾两空格=硬换行
    return "<p>" + "".join(segs) + "</p>"


def _table(lines, i):
    head = _split_row(lines[i])
    align = [_align(c) for c in _split_row(lines[i + 1])]
    i += 2
    n = len(lines)
    body = []
    while i < n and lines[i].strip() and "|" in lines[i]:
        cells = _split_row(lines[i])
        tds = "".join(f"<td{_al_attr(align[k] if k < len(align) else None)}>{inline(c)}</td>"
                      for k, c in enumerate(cells))
        body.append(f"<tr>{tds}</tr>")
        i += 1
    ths = "".join(f"<th{_al_attr(align[k] if k < len(align) else None)}>{inline(c)}</th>"
                  for k, c in enumerate(head))
    tb = f"<tbody>{''.join(body)}</tbody>" if body else ""
    return f"<table><thead><tr>{ths}</tr></thead>{tb}</table>", i


def _list_block(lines, i):
    """解析列表（缩进进一级嵌套）。lines[i] 必须是列表项行，返回 (html, 下一行下标)"""
    m0 = _RE_ITEM.match(lines[i])
    base = len(m0.group(1))
    ordered = m0.group(2)[0].isdigit()
    items = []
    n = len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            nm = _RE_ITEM.match(lines[j]) if j < n else None
            if j < n and ((nm and len(nm.group(1)) > base)
                          or (not nm and lines[j][:1] in (" ", "\t"))):
                items[-1].append("")   # 空行后仍是本级内容：宽松列表，保留空行
                i = j
                continue
            break
        m = _RE_ITEM.match(ln)
        if m and len(m.group(1)) <= base:
            items.append([m.group(3)])
            i += 1
            continue
        if ln[:1] in (" ", "\t"):
            items[-1].append(_dedent(ln, base + 2))   # 缩进续行并入当前项
            i += 1
            continue
        break
    tight = all(not any(not x.strip() for x in it) for it in items)   # 无空行=紧凑列表
    lis = []
    for it in items:
        while it and not it[-1].strip():
            it.pop()
        blocks = _blocks(it)
        if tight and blocks and blocks[0].startswith("<p>") and blocks[0].endswith("</p>"):
            blocks[0] = blocks[0][3:-4]
        lis.append("<li>" + "".join(blocks) + "</li>")
    tag = "ol" if ordered else "ul"
    return f"<{tag}>{''.join(lis)}</{tag}>", i


def _blocks(lines):
    out, i, n = [], 0, len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = _RE_FENCE.match(ln)
        if m:                                    # 围栏代码块：语言标记忽略，内容原样转义
            mark = m.group(1)
            j = i + 1
            code = []
            while j < n:
                cm = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", lines[j])
                if cm and cm.group(1)[0] == mark[0] and len(cm.group(1)) >= len(mark):
                    break
                code.append(lines[j])
                j += 1
            out.append("<pre><code>" + esc("\n".join(code)) + "</code></pre>")
            i = j + 1 if j < n else n
            continue
        if _is_table_start(lines, i):
            html, i = _table(lines, i)
            out.append(html)
            continue
        if _RE_HR.match(ln):
            out.append("<hr>")
            i += 1
            continue
        m = _RE_HEAD.match(ln)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2) or '')}</h{lvl}>")
            i += 1
            continue
        if _RE_QUOTE.match(ln):
            q = []
            while i < n and _RE_QUOTE.match(lines[i]):
                q.append(_RE_QUOTE.sub("", lines[i], count=1))
                i += 1
            out.append("<blockquote>" + "".join(_blocks(q)) + "</blockquote>")
            continue
        if _RE_ITEM.match(ln):
            html, i = _list_block(lines, i)
            out.append(html)
            continue
        para = []
        while i < n and lines[i].strip() \
                and not _RE_FENCE.match(lines[i]) and not _RE_HEAD.match(lines[i]) \
                and not _RE_HR.match(lines[i]) and not _RE_QUOTE.match(lines[i]) \
                and not _RE_ITEM.match(lines[i]) and not _is_table_start(lines, i):
            para.append(lines[i])
            i += 1
        out.append(_paragraph(para))
    return out


def md_render(text):
    """markdown 文本 → HTML 片段（不含 <html> 外壳）"""
    return "".join(_blocks(text.splitlines()))


def md_page(name, body):
    """把渲染好的 HTML 片段包成完整页面（内联样式，色板跟主 UI 一致）"""
    head = ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f"<title>{esc(name)}</title><style>{_MD_CSS}</style></head>\n<body><main>\n")
    return head + body + "\n</main></body></html>"


def _md_selftest():
    """渲染器自检：python3 app.py --selftest"""
    n_ok = 0
    fails = []

    def ck(name, cond, got):
        nonlocal n_ok
        if cond:
            n_ok += 1
        else:
            fails.append((name, got))

    h = md_render("# 一级\n## 二级\n###### 六级\n####### 七级不算")
    ck("ATX 标题", "<h1>一级</h1>" in h and "<h2>二级</h2>" in h
       and "<h6>六级</h6>" in h and "<h7" not in h and "七级不算" in h, h)

    c = md_render("```python\nprint('<b>')\n**not bold**\n```")
    ck("围栏代码块", "<pre><code>" in c and "print('&lt;b&gt;')" in c
       and "<strong>" not in c and "<b>" not in c, c)

    t = md_render("| 左 | 中 | 右 |\n|:---|:---:|---:|\n| a | b | c |")
    ck("表格", "<table>" in t and "<th" in t and ">a</td>" in t
       and 'text-align:center' in t and 'text-align:right' in t, t)

    i = md_render("行内：`co&de`、**粗**、*斜*、~~删~~、[站](https://e.com/a?x=1&y=2)")
    ck("行内格式", "<code>co&amp;de</code>" in i and "<strong>粗</strong>" in i
       and "<em>斜</em>" in i and "<del>删</del>" in i, i)
    ck("https 链接", '<a href="https://e.com/a?x=1&amp;y=2">站</a>' in i, i)

    l = md_render("- 甲\n- 乙\n  - 乙一\n\n1. 一\n2. 二")
    ck("无序列表", "<ul><li>甲</li>" in l, l)
    ck("嵌套列表", "<li>乙<ul><li>乙一</li></ul></li>" in l, l)
    ck("有序列表", "<ol><li>一</li><li>二</li></ol>" in l, l)

    q = md_render("> 引用一层\n> \n> > 嵌套引用")
    ck("引用", "<blockquote>" in q and "引用一层" in q and q.count("<blockquote>") == 2, q)

    r = md_render("上文\n\n---\n\n***\n\n下文")
    ck("分割线", r.count("<hr>") == 2 and "上文" in r and "下文" in r, r)

    p = md_render("![图](./img/a.png)")
    ck("相对路径图片", '<img src="./img/a.png" alt="图">' in p, p)

    m = md_render("[锚](#top) [邮](mailto:a@b.c) [上](../up.md)")
    ck("白名单链接", '<a href="#top">锚</a>' in m and '<a href="mailto:a@b.c">邮</a>' in m
       and '<a href="../up.md">上</a>' in m, m)

    x1 = md_render("<script>alert(1)</script>")
    ck("XSS: script 转义", "&lt;script&gt;alert(1)&lt;/script&gt;" in x1 and "<script>" not in x1, x1)

    x2 = md_render("[点我](javascript:alert(1))")
    ck("XSS: javascript: 降级", "<a" not in x2 and "javascript:alert(1)" in x2, x2)

    x3 = md_render("![x](data:text/html,<script>alert(1)</script>)")
    ck("XSS: data: 图片降级", "<img" not in x3, x3)

    x4 = md_render('<img src=x onerror="alert(1)">')
    ck("XSS: 裸 HTML 转义", "&lt;img" in x4 and "<img" not in x4, x4)

    x5 = md_render("[a](vbscript:msgbox(1)) [b](//evil.example)")
    ck("XSS: vbscript/协议相对降级", "<a" not in x5, x5)

    x6 = md_render('[q](https://e.com/"x")')
    ck("URL 引号转义", 'href="https://e.com/&quot;x&quot;"' in x6, x6)

    su = [safe_url("\\\\evil.com"), safe_url("/\\evil.com"), safe_url("\\/evil.com")]
    ck("safe_url 反斜杠协议相对", all(x is None for x in su), str(su))

    bs = md_render("[逃逸](\\\\evil.com)")
    ck("反斜杠协议相对链接降级", "<a" not in bs and "evil.com" in bs, bs)

    ci = md_render("[`*code*`](https://example.com)")
    ck("链接文本内代码不被污染", '<a href="https://example.com"><code>*code*</code></a>' in ci, ci)

    ti = md_render('[官网](https://example.com "首页")')
    ck("title 语法降级纯文本", "<a" not in ti and "首页" in ti, ti)

    pg = md_page("<hi>.md", md_render("# 页"))
    ck("整页包装", "<!doctype html>" in pg and "<title>&lt;hi&gt;.md</title>" in pg
       and "<style>" in pg and "--ink:#1d1d1f" in pg and "<h1>页</h1>" in pg, pg)

    for name, got in fails:
        print(f"FAIL {name} → {got[:200]}")
    if fails:
        print(f"自检失败：{len(fails)}/{n_ok + len(fails)} 项未过")
        return 1
    print(f"自检通过：{n_ok} 项")
    return 0


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


def restore_format(orig, text):
    """按原文件格式编码：保留 UTF-8 BOM；原文件以 CRLF 为主则换行还原为 CRLF"""
    bom = orig.startswith(b"\xef\xbb\xbf")
    crlf = orig.count(b"\r\n") * 2 > orig.count(b"\n")
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    if crlf:
        text = text.replace("\n", "\r\n")
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def atomic_write(p, data, mode):
    """同目录临时文件写完再 os.replace：中途失败不会留下半截文件"""
    tmp = p.with_name(f".{p.name}.fileweb-{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


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

    def _multipart(self):
        """解析 multipart 上传，返回 (普通字段 dict, file part)；非 multipart 抛 ValueError"""
        # email 解析需要消息头，补上 Content-Type 再喂给 BytesParser
        hdr = f"MIME-Version: 1.0\r\nContent-Type: {self.headers.get('Content-Type', '')}\r\n\r\n"
        msg = BytesParser(policy=policy.default).parsebytes(hdr.encode() + self._body())
        if not msg.is_multipart():
            raise ValueError("不是上传表单")
        fields, fp = {}, None
        for part in msg.iter_parts():
            nm = part.get_param("name", header="content-disposition")
            if nm == "file":
                fp = part
            elif nm:
                fields[nm] = part.get_payload(decode=True).decode()
        return fields, fp

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
        if p.suffix.lower() in (".md", ".markdown"):
            # markdown 预览：渲染成完整 HTML 页（同源 iframe，样式内联）
            try:
                text = p.read_bytes().decode("utf-8-sig", errors="replace")
            except OSError as e:
                return self._send(500, f"读取失败: {e}", "text/plain; charset=utf-8")
            return self._send(200, md_page(p.name, md_render(text)))
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
        mtime = str(p.stat().st_mtime_ns)          # 先取 mtime 再读：竞态时只会多报冲突，不会漏报
        raw = p.read_bytes()
        if b"\x00" in raw:
            return self._json({"error": "二进制文件，无法编辑"}, 400)
        # 去 BOM、CRLF→LF：与 textarea 归一化后的值一致，保存时再按原文件格式还原
        try:
            text, readonly = raw.decode("utf-8-sig"), False
        except UnicodeDecodeError:              # 非 UTF-8：只读展示，绝不写回
            text, readonly = raw.decode("utf-8-sig", errors="replace"), True
        return self._json({"path": rel, "content": text.replace("\r\n", "\n"), "readonly": readonly, "mtime": mtime})

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
                orig = p.read_bytes()
                try:
                    orig.decode("utf-8")
                except UnicodeDecodeError:
                    return self._json({"error": "非 UTF-8 文件，只读"}, 400)
                st = p.stat()
                if data.get("mtime") and not data.get("force") and data["mtime"] != str(st.st_mtime_ns):
                    return self._json({"error": "文件已在别处被修改", "conflict": True}, 409)
                atomic_write(p, restore_format(orig, data.get("content", "")), st.st_mode)
            return self._json({"ok": True, "mtime": str(p.stat().st_mtime_ns)})
        if u.path == "/api/delete":
            return self.api_delete()
        if u.path == "/api/create":
            return self.api_create()
        if u.path == "/api/rename":
            return self.api_rename()
        if u.path == "/api/upload":
            return self.api_upload()
        if u.path == "/api/deploy":
            return self.api_deploy()
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

    def api_create(self):
        """在当前目录新建文件夹或空文件；重名直接报错，不覆盖不改名"""
        try:
            data = json.loads(self._body().decode())
            p = safe_path(data.get("path", ""))
        except (PermissionError, ValueError) as e:
            return self._json({"error": f"请求无效: {e}"}, 400)
        name = (data.get("name") or "").strip()
        kind = data.get("type")
        err = check_name(name)
        if err:
            return self._json({"error": err}, 400)
        if kind not in ("dir", "file"):
            return self._json({"error": "类型无效"}, 400)
        if not p.is_dir():
            return self._json({"error": "当前目录不存在"}, 400)
        dest = p / name
        with _LOCK:                                     # 检查与创建同锁，防并发覆盖同名
            if dest.is_symlink() or dest.exists():      # 断链软链 exists() 为假，会顺链写出去
                return self._json({"error": "已存在同名，请换个名字"}, 400)
            try:
                dest.mkdir() if kind == "dir" else dest.write_text("", encoding="utf-8")
            except OSError as e:
                return self._json({"error": f"新建失败: {e}"}, 400)
        return self._json({"ok": True, "path": str(dest.relative_to(ROOT))})

    def api_rename(self):
        """重命名文件或文件夹；新名已存在则报错，不覆盖"""
        try:
            data = json.loads(self._body().decode())
            p = safe_path(data.get("path", ""))
        except (PermissionError, ValueError) as e:
            return self._json({"error": f"请求无效: {e}"}, 400)
        name = (data.get("name") or "").strip()
        err = check_name(name)
        if err:
            return self._json({"error": err}, 400)
        if p == ROOT:
            return self._json({"error": "不能重命名根目录"}, 400)
        if p.is_symlink() or not p.exists():
            return self._json({"error": "文件或文件夹不存在"}, 400)
        dest = p.with_name(name)
        with _LOCK:                                     # 检查与改名同锁，防并发覆盖同名
            if dest.is_symlink() or dest.exists():
                return self._json({"error": "已存在同名，请换个名字"}, 400)
            try:
                p.rename(dest)
            except OSError as e:
                return self._json({"error": f"重命名失败: {e}"}, 400)
        return self._json({"ok": True, "path": str(dest.relative_to(ROOT))})

    def api_upload(self):
        try:
            try:
                fields, fp = self._multipart()
            except ValueError:
                return self._json({"error": "不是上传表单"}, 400)
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
            if dest.is_symlink():      # 断链软链 exists() 为假，不拦就会顺链写到 ROOT 外
                return self._json({"error": "已存在同名软链，换个名字再传"}, 400)
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

    def api_deploy(self):
        """上传 <项目名>.zip → 覆盖式解压进暂存仓 → 提交推送 → 真仓 ff-only 拉取"""
        try:
            _, fp = self._multipart()      # 表单只需要文件 part，普通字段用不上
            if fp is None or not fp.get_filename():
                return self._json({"error": "没有文件"}, 400)
            # 文件名只取 basename（防路径穿越），去 .zip 后缀即项目名
            name = os.path.basename(fp.get_filename().replace("\\", "/")).removesuffix(".zip")
            err = check_name(name)
            if err:
                return self._json({"error": err}, 400)
            data = fp.get_payload(decode=True)
            if len(data) > MAX_UPLOAD_SIZE:
                return self._json({"error": f"超过 {human(MAX_UPLOAD_SIZE)} 上限"}, 400)
            with _LOCK:
                ok, err, log = deploy_zip(name, data)
            if not ok:
                return self._json({"error": err, "log": log}, 400)
            return self._json({"ok": True, "log": log})
        except Exception as e:
            return self._json({"error": f"上传失败: {e}"}, 400)


def main():
    port = int(os.environ.get("ONE_FILES_PORT", "8766"))
    if not PASSWORD:
        sys.exit("未设置密码：需要 ONE_FILES_PASSWORD 或 common_password 环境变量")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_md_selftest())
    main()
