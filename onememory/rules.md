# Project Rules Memory

（本文件记录行为规则与项目知识，上限 150 行，超限触发同类合并。每条规则正文 ≤100 字、建议约 60 字，只写「动作 + 关键对象」；纯命令属知识，不入规范。）

## Entries

fileweb 服务为常驻进程，改 app.py 后必须重启才生效
- Date: 2026-09-18
- Category: Operations & Deployment
- Instructions:
  - fileweb 常驻进程：改 app.py 需 kill 重启，改 index.html 不用
  - 启动需 `ONE_FILES_PASSWORD`（或 common_password），对外只走 tailscale serve

记忆沉淀按「一件事」记账，不按改动次数
- Date: 2026-09-18
- Category: Workflow & Collaboration
- Instructions:
  - 同一会话 ID 在 timeline.md 与海马流水只占一行总摘要；后续微调改写那行，不追加

UI 一律走极简：不要渐变、不要阴影、不要装饰性动效
- Date: 2026-09-18
- Category: Workflow & Collaboration
- Instructions:
  - 悬浮按钮/控件扁平：不要渐变、不要阴影、不要弹性缩放
  - 最终外观以用户当场拍板为准，多轮微调一次到位

重启 fileweb 服务必须清掉 Agent 会话环境变量
- Date: 2026-09-19
- Category: Operations & Deployment
- Instructions:
  - 用 Agent Bash 重启会把会话 ID 带进常驻进程，致服务端 git commit 撞记忆门禁失败
  - 重启须先清 `CODEBUDDY_SESSION_ID`/`CLAUDE_SESSION_ID` 再起进程，且勿用 `pkill -f` 误杀自身
