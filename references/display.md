# 展示结果

脚本成功后返回 `path`（原始绝对路径）、`display_path`（适合 Markdown 的绝对路径）和 `runtime_os`。

## 规则

- Markdown 图片或链接只用 `display_path`，绝不放原始 `path`，也不要手工拼接路径。
- 不使用 `file://` URL 或反斜杠。
- `runtime_os` 为 `windows` 时，`display_path` 必须带盘符并使用正斜杠，例如 `C:/Users/name/.claude/skills/gd-image-gen/generated_images/image.png`。
- `runtime_os` 为 `macos` 或 `linux` 时，`display_path` 必须以 `/` 开头。
- `runtime_os` 不受支持、`display_path` 缺失或形式与系统不符时，不要输出会坏掉的链接，改为报告校验失败。
- 展示前不要做视觉检查，也不要调用看图工具。

## 写法

```markdown
![生成的图片](<display_path>)

[打开本地图片](<display_path>)
```

## 不同宿主

- 宿主提供「向用户发送文件」类工具（例如 Claude 桌面端的文件卡片、WorkBuddy 的附件发送）时，可以直接用 `path` 发送图片，同时在文字中给出 `display_path` 链接。
- 只渲染 Markdown 的宿主（Codex、DeepSeek Harness 终端等）用上面的 Markdown 写法。终端无法内联显示图片时，链接仍可点击打开本地文件。
- agent 运行在远程主机或容器中时，路径指向的是那台机器上的文件；如实告知用户文件位置，不要改写路径。
