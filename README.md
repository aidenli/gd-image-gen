# GD Image Gen

通过用户自备的 OpenAI 兼容 Images 接口，以直连非流式请求生成和编辑位图。

本技能只接受完整、经过校验的图片数据作为输出。它把校验通过的图片写入技能根目录下的 `generated_images`，并返回适合当前操作系统的、可安全展示的绝对路径。

## 安装

本技能遵循 [Agent Skills](https://agentskills.io) 目录约定（`SKILL.md` + `scripts/` + `references/`），可直接放进任何支持该约定的 agent 的技能目录。脚本按自身位置定位 `.env` 与 `generated_images`，装在哪里都能用。

| Agent | 用户级安装目录 | 项目级安装目录 |
|---|---|---|
| Claude Code | `~/.claude/skills/gd-image-gen` | `.claude/skills/gd-image-gen` |
| Codex | `${CODEX_HOME:-~/.codex}/skills/gd-image-gen` 或 `~/.agents/skills/gd-image-gen` | `.agents/skills/gd-image-gen` |
| DeepSeek Harness | `~/.agents/skills/gd-image-gen` 或 `${DSH_HOME:-~/.dsh}/skills/gd-image-gen` | `.agents/skills/gd-image-gen` 或 `.dsh/skills/gd-image-gen` |
| WorkBuddy | `~/.workbuddy/skills/gd-image-gen` 或 `~/.agents/skills/gd-image-gen` | `.workbuddy/skills/gd-image-gen` |

`~/.agents/skills` 被 Codex、DeepSeek Harness 和 WorkBuddy 共同识别，多个 agent 共用时装在这里最省事：

```sh
git clone https://github.com/aidenli/gd-image-gen.git ~/.agents/skills/gd-image-gen
```

Claude Code 不读取 `~/.agents/skills`，单独安装，或用软链接共享同一份（共享后 `.env` 与生成的图片也共用）：

```sh
ln -s ~/.agents/skills/gd-image-gen ~/.claude/skills/gd-image-gen
```

更新：

```sh
git -C ~/.agents/skills/gd-image-gen pull --ff-only
```

装好后重启 agent 或开新会话，让它重新扫描技能目录。`agents/openai.yaml` 是 Codex 专用的界面元数据，其它 agent 会忽略它。

## 依赖与配置

先安装 [`uv`](https://docs.astral.sh/uv/)，然后在技能目录下用本地浏览器表单配置：

```sh
uv run scripts/stream_image.py configure --web
```

配置是按需进行的：在准备生成或编辑图片时运行，不要在安装或健康检查阶段运行。技能的 agent 指令要求表单只在「确有图片请求、且配置缺失或无效」时出现，因此安装过程不会弹出浏览器窗口。

脚本会在 `127.0.0.1` 上启动一个一次性表单（随机端口、随机令牌），用默认浏览器打开，并把提交的值直接写入技能根目录的 `.env`。API Key 由浏览器直接交给这个本地进程 —— 不会经过 AI 对话、shell 参数或命令历史。保存后表单立即退出；用 `--no-browser` 可改为打印地址而不打开浏览器，用 `--timeout` 可修改 300 秒的等待时长。

随时查看当前状态（只读，绝不打开表单）：

```sh
uv run scripts/stream_image.py status            # 输出 ready、base_url 和掩码后的 Key
uv run scripts/stream_image.py status --require  # 配置不完整时退出码为 2
```

其它单独更新某项设置的方式：

```sh
uv run scripts/stream_image.py configure --base-url "https://example.com/v1"
uv run scripts/stream_image.py configure --api-key   # 隐藏的终端输入或 stdin
```

留空会保留原值；自动化场景通过 stdin 传入 Key，绝不作为命令行参数值。`save-key` 仍然兼容。设置以原子方式写入技能根目录、已被 git 忽略的 `.env` 中的 `OPENAI_BASE_URL` 与 `OPENAI_API_KEY`，保留其它设置和注释。Key 以本地明文存储。手动配置可参考 `.env.example`。

配置只由用户提供。不会回退到任何 agent 的配置或登录文件（如 Codex 的 `config.toml`、`auth.json`），因此缺失的设置总会被报告出来，而不会悄悄借用别的凭据。进程环境变量也不是这两项设置的来源。

设置缺失或无效时，会在发出任何 API 请求之前一并报告，并给出打开配置表单的确切命令。URL 必须使用 HTTP(S)，且不含内嵌凭据、查询参数或片段。错误信息会区分连接失败、超时、认证、权限、接口不存在、限流/额度与服务端失败，同时不暴露服务商原始消息或 Key。保存配置只校验格式，不代表连通性或 Key 有效性已被验证。

默认模型为 `gpt-image-2.5-flare`；需要时用 `--model` 指定 `gpt-image-2.5-sunburst` 或 `gpt-image-2`。

## 使用

每个所需图片运行一条命令。`uv run` 会在需要时安装脚本的 Python 依赖。

```sh
uv run ~/.agents/skills/gd-image-gen/scripts/stream_image.py generate \
  --prompt "木桌上的一盏红色纸灯笼，棚拍产品图" \
  --size auto \
  --quality medium \
  --timeout 600
```

编辑图片时显式传入每一张输入图：

```sh
uv run ~/.agents/skills/gd-image-gen/scripts/stream_image.py edit \
  --prompt "把背景换成清晨安静的城市街道；保持主体不变" \
  --image /absolute/path/to/input.png \
  --size auto \
  --quality medium \
  --timeout 600
```

只有在提供了 mask 时才用 `--mask /absolute/path/to/mask.png`。多张源图就重复 `--image`。

## 透明背景

需要透明底的素材（图标、贴纸、Logo、抠图）时加 `--background transparent`，输出格式用 `png`（默认）或 `webp`：

```sh
uv run ~/.agents/skills/gd-image-gen/scripts/stream_image.py generate \
  --prompt "一只卡通橘猫贴纸，主体边缘清晰，无背景，无投影" \
  --background transparent \
  --output-format png \
  --size auto \
  --timeout 600
```

- `--background transparent` 搭配 `--output-format jpeg` 会在发请求前报错，因为 JPEG 没有透明通道。
- 结果里的 `has_transparency` 表示保存的图片是否真的含透明像素。请求了透明但返回不透明图时，结果会带 `warning`，通常说明服务商或模型不支持该参数。
- 是否生效取决于你接入的服务商和模型。

## 运行规则

- 超时需大于 120 秒且不超过 600 秒。
- 不要仅因为请求已超过 2 分钟就终止它。
- 在结果完成、出现明确的终止性失败，或到达配置的超时时停止。
- 重试方式不得让单个所需产物超过 10 分钟运行时间。

## 结果约定

成功时，stdout 输出一个 JSON 对象，包含 `ok: true`、输出元数据、SHA-256、请求模式、`display_path`、`background` 和 `has_transparency`。在 agent 回复中渲染本地图片时使用 `display_path`（规则见 [references/display.md](references/display.md)）。

失败时，stderr 输出一个 `ok: false` 的 JSON 对象。响应中没有完整图片数据即视为失败。

## 兼容性说明

- 各 agent 的 shell 工具默认超时不同（Claude Code 默认 2 分钟）。`SKILL.md` 要求 agent 把超时调到至少 610 秒或转后台等待，否则长请求会被宿主提前杀掉。
- 宿主若内置图片工具，技能说明要求优先使用本技能，失败也不切换。
- 下面示例路径以 `~/.agents/skills` 为例，按你的实际安装位置替换。

## 文件结构

```
SKILL.md                    agent 入口：工作流、命令、运行时限
references/configuration.md 配置与凭据规则（按需加载）
references/display.md       结果展示规则（按需加载）
scripts/stream_image.py     生成、编辑、配置脚本（uv 单文件脚本）
scripts/test_auth.py        配置与错误处理自检
agents/openai.yaml          Codex 界面元数据
.env.example                手动配置模板
```

完整的 agent 工作流与约束见 [SKILL.md](SKILL.md)。
