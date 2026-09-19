# Timeline

- 2026-09-18 [fw-20260918-a1] fileweb 加号入口改造：上传按钮改为菜单（选择文件上传 / 同步 GitHub 仓库），新增 /api/sync 做 pull→add→commit debug→push；按钮外观多轮调整后定为蓝底圆形白色加号（最终右 21px、下 -10px），服务已重启
- 2026-09-19 18:37 [fw-20260919-a1] fileweb 编辑器新增 Ctrl/Cmd+S 保存快捷键：全局 keydown 拦截浏览器“保存网页”，编辑器打开时调用 save()
