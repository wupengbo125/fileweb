# fileweb

手机优先的极简文件查看 / 编辑器。零依赖，只用 Python 标准库。

手机上"看列表 + 改文件"这件事，用不了桌面 IDE（code-server 在手机上只是个缩小的桌面端），也不需要装客户端。fileweb 就是为此做的：单页、手机尺寸优先、能读能存。

## 特性

- 零依赖：`app.py`（服务端）+ `index.html`（前端），仅用 Python 标准库
- 手机优先 UI：frosted 导航 / 分组列表 / SVG 图标 / 单一强调色
- 密码登录：HttpOnly Cookie 会话，有效期 30 天
- 路径越界防护：`resolve()` + `is_relative_to()` 双重判定，出不了根目录
- 安全边界：只读 2MB 以内的 UTF-8 文本文件，二进制拒绝编辑
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

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/list?path=` | 列目录，返回 dirs + files |
| GET | `/api/file?path=` | 读文本文件内容 |
| POST | `/api/file` | `{"path": "", "content": ""}` 写文件 |

## License

MIT
