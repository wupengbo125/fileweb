# fileweb

手机优先的极简文件查看 / 编辑器。零依赖，只用 Python 标准库。

手机上"看列表 + 改文件"这件事，用不了桌面 IDE（code-server 在手机上只是个缩小的桌面端），也不需要装客户端。fileweb 就是为此做的：单页、手机尺寸优先、能读能存。

## 特性

- 零依赖：`app.py`（服务端）+ `index.html`（前端），仅用 Python 标准库
- 手机优先 UI：frosted 导航 / 分组列表 / SVG 图标 / 单一强调色
- 密码登录：HttpOnly Cookie 会话，有效期 30 天
- 路径越界防护：`resolve()` + `is_relative_to()` 双重判定，出不了根目录
- 安全边界：只在线编辑 2MB 以内的文本文件；非 UTF-8 只读打开、绝不写回；二进制拒绝编辑
- 保存不破坏文件：保留原有 CRLF / UTF-8 BOM；打开后被别处改过会提示（409）；临时文件 + `os.replace` 原子写入
- 图片（png / jpg / gif / webp）直接预览；SVG 预览放在 `<img>` 中，不执行脚本
- 默认只监听 `127.0.0.1`，公网暴露交给 Tailscale

## 用法

```bash
ONE_FILES_PASSWORD=xxx ONE_FILES_PORT=8766 python3 app.py
```

打开 http://127.0.0.1:8766 ，输入密码即可。

### 环境变量

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `ONE_FILES_ROOT` | 文件浏览根目录 | `~/onespace/github` |
| `ONE_FILES_PASSWORD` | 登录密码（必填其一） | — |
| `common_password` | 登录密码，`ONE_FILES_PASSWORD` 的兜底 | — |
| `ONE_FILES_PORT` | 监听端口 | `8766` |
| `ONE_SYNC_BASE` | 项目包同步的暂存仓根目录（提交推送源） | `~/onespace/hsgithub` |

### 公网访问

只监听回环地址，需要外部访问时用 Tailscale 暴露：

```bash
tailscale serve --https 8766 --bg 8766
```

### systemd 常驻

示例 unit：

```ini
[Service]
ExecStart=/bin/bash -c '. ~/onespace/github/dotfiles/rc/bash/exports && \
  export ONE_FILES_PASSWORD="$common_password" ONE_FILES_ROOT="$HOME/onespace/github" && \
  exec python3 /path/to/fileweb/app.py'
Restart=always
```

注意 user session 不继承 bashrc，必须显式 source 环境变量。

## 界面

- **手机（<768px）**：单列列表 + 全屏编辑器；右下 + 菜单（新建文件夹 / 文件、上传、同步项目包、同步 GitHub、显示隐藏文件）；左滑删除、长按重命名。
- **≥768px**：列表限宽居中，显示可点击的面包屑路径；鼠标悬停行显示「重命名 / 删除」。
- **≥1024px**：左侧列表 | 右侧编辑器并排，打开文件时目录仍可见；按 `/` 过滤当前目录（Esc 清除，Enter 打开第一项）。
- 当前目录与打开的文件写在 URL hash（`#d=目录&f=文件`），刷新不丢位置；安卓 / PWA 返回键回上一层或关闭编辑器。
- 以 `.` 开头的文件默认隐藏，可在 + 菜单切换（存在 localStorage）。

## API

除登录页和图标 / PWA 静态资源外，都需要登录 Cookie（`of_session`），未登录一律 302 到 `/login`。`path` 均为相对 `ONE_FILES_ROOT` 的路径，越界返回 403。出错时返回 `{"error": "..."}`。

| 方法 | 路径 | 参数 | 说明 |
| --- | --- | --- | --- |
| GET | `/login` | — | 登录页 |
| POST | `/login` | 表单 `password` | 校验通过设置会话 Cookie（30 天）并跳转 `/` |
| GET | `/`、`/index.html` | — | 前端单页 |
| GET | `/manifest.json`、`/sw.js`、`/favicon*.png`、`/apple-touch-icon.png`、`/icon-*.png` | — | PWA / 图标静态资源，免登录 |
| GET | `/api/list` | `?path=` | 列目录，目录在前、按名称排序：`{path, parent, entries: [{name, type, size, mtime}]}` |
| GET | `/api/file` | `?path=` | 读文本文件：`{path, content, readonly, mtime}`。内容已去 BOM、CRLF 转 LF；非 UTF-8 时 `readonly: true`；超过 2MB 或二进制返回 400 |
| POST | `/api/file` | `{path, content, mtime?, force?}` | 写文件。按磁盘原文件还原 BOM / CRLF，原子写入；`mtime` 与磁盘不一致且无 `force` 时返回 **409** `{conflict: true}`；非 UTF-8 原文件返回 400。成功 `{ok, mtime}` |
| GET | `/raw/<path>` | — | 原样返回文件字节（按扩展名给 Content-Type）；`.md` / `.markdown` 返回渲染后的 HTML 预览页；目录有 `index.html` 时返回它 |
| POST | `/api/create` | `{path, name, type: "file" \| "dir"}` | 在 `path` 目录下新建空文件或文件夹，重名返回 400；成功 `{ok, path}` |
| POST | `/api/rename` | `{path, name}` | 原地改名（同目录），重名返回 400；成功 `{ok, path}` |
| POST | `/api/delete` | `{path}` | 立即删除文件或整个文件夹，不可撤销；不能删根目录 |
| POST | `/api/upload` | multipart：`path`、`file` | 上传到 `path` 目录，上限 200MB；重名自动改为 `name 1.ext`；成功 `{ok, name}` |
| POST | `/api/sync` | `{path}` | 同步 `path` 所在 Git 仓库：pull → commit → push；在根目录时并发同步其下所有仓库。返回 `{ok, repo, log}` 或 `{error, log}` |
| POST | `/api/deploy` | multipart：`file`（`<项目名>.zip`） | 覆盖式解压进 `ONE_SYNC_BASE/<项目名>` → 提交推送 → `ONE_FILES_ROOT/<项目名>` ff-only 拉取；上限 200MB。返回 `{ok, log}` 或 `{error, log}` |

zip 文件名（去掉 `.zip`）就是项目名，真仓取 `ONE_FILES_ROOT/<项目名>`；暂存仓不存在时从真仓拷 `.git` 建出来。

## License

MIT
