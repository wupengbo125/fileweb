# Timeline

- 2026-09-18 [fw-20260918-a1] fileweb 加号入口改造：上传按钮改为菜单（选择文件上传 / 同步 GitHub 仓库），新增 /api/sync 做 pull→add→commit debug→push；按钮外观多轮调整后定为蓝底圆形白色加号（最终右 21px、下 -10px），服务已重启
- 2026-09-19 18:37 [fw-20260919-a1] fileweb 编辑器新增 Ctrl/Cmd+S 保存快捷键（全局 keydown 拦截浏览器“保存网页”）；同步重构为 sync_repo()，在 GitHub 根目录时并发（ThreadPoolExecutor 8 线程）同步其下全部 11 个仓库，全成功提示“已同步 N 个仓库”，部分失败列出失败仓库名（最多 3 个）
- 2026-09-19 23:06 [afab80b4-72eb-479e-9181-da79b0ab685a] fileweb 编辑器：新增行号栏（textarea 外包 #edbody，左侧 #gutter 渲染 1..N，input 重算、滚动 translateY 同步）；新增 Ctrl+X 剪切整行（仅无选区时接管，选区扩至整行后走 execCommand("cut") 写剪贴板并保留 undo，失败则 setRangeText 删除 + clipboard.writeText 兜底）；失败提示改为常驻弹窗（#dlg 展示错误信息与同步 log，须手动关闭），成功改为居中绿色对勾闪现 0.5s；右上角“前进”按钮左侧新增刷新图标（前进按钮保留；非编辑态刷新目录，编辑态重载当前文件，有未保存修改时 confirm 确认）
