# Project Rules Memory

（本文件记录行为规则与项目知识，上限 150 行，超限触发同类合并。）

## Entries

fileweb 服务为常驻进程，改 app.py 后必须重启才生效
- Date: 2026-09-18
- Context: Discovered by Agent while performing 加号菜单与仓库同步功能开发
- Category: Operations & Deployment
- Instructions:
  - 线上服务以 `python3 /home/ctyun/onespace/github/fileweb/app.py` 常驻运行（非 supervisor 托管）；改 app.py 需 kill 旧进程重启，改 index.html 不需要（每次请求都重新读盘）。
  - 启动需要环境变量 `ONE_FILES_PASSWORD`（或 common_password）与可选的 `ONE_FILES_ROOT`、`ONE_FILES_PORT`；监听 127.0.0.1，对外用 `tailscale serve --https <PORT> --bg <PORT>`。

UI 一律走极简：不要渐变、不要阴影、不要装饰性动效
- Date: 2026-09-18
- Context: 用户否定超椭圆 squircle + 渐变 + 阴影版加号按钮，要求回归微信式极简
- Category: Workflow & Collaboration
- Instructions:
  - 悬浮按钮/控件保持扁平：纯色正圆（或圆角矩形）+ 单一描边图标即可，禁止渐变、阴影、弹性缩放等修饰。
  - 图标笔画偏细（加号类 stroke-width 1.8 左右），留白足够，克制的比花哨的更高级。
