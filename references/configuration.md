# 配置与凭据

`<SKILL_DIR>` 指 `SKILL.md` 所在目录。配置保存在 `<SKILL_DIR>/.env` 的 `OPENAI_BASE_URL` 与 `OPENAI_API_KEY`，该文件已被 git 忽略，以本地明文存储。

## 何时配置

配置是惰性的。只有以下条件同时成立时才启动配置表单：

1. 用户此刻确实要求生成或编辑图片，而不是在问技能能做什么、怎么配置，或让你安装、更新、验证技能。
2. `status` 显示 `ready: false`，或 `generate`/`edit` 刚刚因配置错误失败。

不要做：

- 不要在安装、升级、自检或「装好了吗」这类任务中运行 `configure`、`configure --web` 或 `save-key`。
- 不要因为技能被提及、加载或文档被读取就启动表单，也不要「先准备好以后用」。
- 不要同时开第二个表单；已有一个在等待时，让用户去看那个页面。

推荐做法：直接运行 `generate`/`edit`（不确定时先跑 `status`），让缺配置的错误自然出现，用一句话说明缺什么，再启动表单。

## 本地表单

```sh
uv run "<SKILL_DIR>/scripts/stream_image.py" configure --web
```

脚本在 `127.0.0.1` 上以随机端口和一次性令牌提供表单，直接写入 `.env`，保存后退出。对用户说：「请在刚打开的本地页面填写 Base URL 和 API Key，保存后我会继续。」

- 浏览器没自动打开时，把打印出的地址交给用户；无法打开浏览器（远程、沙箱或无界面环境）时加 `--no-browser`。
- 表单进程默认等待 300 秒，可用 `--timeout` 调整。等待期间保持进程运行；若宿主 shell 工具默认超时更短，调高超时或以后台方式运行。
- 超时则说明没有保存任何内容，询问是否重新启动。
- 保存后运行 `status` 确认 `ready: true`，然后继续原请求，不要让用户重复描述需求。

## 状态查询

```sh
uv run "<SKILL_DIR>/scripts/stream_image.py" status            # ready、base_url、掩码 Key
uv run "<SKILL_DIR>/scripts/stream_image.py" status --require  # 不完整时退出码 2
```

`status` 只读，绝不打印完整 Key，也不会打开表单。不要把它当作安装或健康检查的一部分来运行，除非用户要求。

## 其它方式

- `configure --base-url "https://example.com/v1"` 只更新 URL。
- 只有用户坚持用其它方式提供 Key 时，才用 `configure --api-key` 从 stdin 读取（有 TTY 时为隐藏输入）。`save-key` 仍兼容。
- 绝不把 Key 放进命令行参数、shell 字面量、你写的文件或受版本控制的文件。如果 Key 已出现在对话中，只经 stdin 保存，并告知用户它已进入对话记录，建议轮换。
- 留空保留原值；写入是原子的，保留 `.env` 中其它设置和注释。报告成功前确认输出 `ok: true`。

## 规则

- 凭据只来自用户。不会回退到任何 agent 的配置或登录文件（如 Codex 的 `config.toml`、`auth.json`，Claude Code、DeepSeek Harness、WorkBuddy 的设置），进程环境变量也不是来源。不要假设凭据已存在，也不要从这些文件复制。
- Base URL 必须是 HTTP(S)，不含内嵌凭据、查询参数或片段。只去掉末尾斜杠，不要补 `/v1`。
- 保存只校验格式，不代表连通性或 Key 有效性已验证。
- SDK 重试保持关闭。
