# Project Rules Memory

（本文件记录行为规则与项目知识，上限 150 行，超限触发同类合并。）

## Entries

fileweb 服务为常驻进程，改代码后必须重启才生效
- Date: 2026-09-18
- Context: Discovered by Agent while performing 加号菜单与仓库同步功能开发
- Category: Operations & Deployment
- Instructions:
  - 线上服务以 `python3 /home/ctyun/onespace/github/fileweb/app.py` 常驻运行（非 supervisor 托管），修改 app.py/index.html 后需 kill 旧进程并重新启动才生效。
  - 启动需要环境变量 `ONE_FILES_PASSWORD`（或 common_password）与可选的 `ONE_FILES_ROOT`、`ONE_FILES_PORT`；监听 127.0.0.1，对外用 `tailscale serve --https <PORT> --bg <PORT>`。
