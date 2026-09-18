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

记忆沉淀按「一件事」记账，不按改动次数
- Date: 2026-09-18
- Context: 用户指出同一会话 ID 不该每次微调都写一行摘要
- Category: Workflow & Collaboration
- Instructions:
  - 同一会话 ID 在 timeline.md 与海马每日流水中只占一行总摘要；后续微调直接改写那一行，不追加新行。

UI 一律走极简：不要渐变、不要阴影、不要装饰性动效
- Date: 2026-09-18
- Context: 用户否定超椭圆 squircle + 渐变 + 阴影版加号按钮，要求回归微信式极简
- Category: Workflow & Collaboration
- Instructions:
  - 悬浮按钮/控件保持扁平：不要渐变、不要阴影、不要弹性缩放。
  - 克制优先，少即是多；但最终外观以用户当场拍板为准，多轮微调要一次到位、别来回加装饰。
