---
name: gd-image-gen
description: "通过用户自备的 OpenAI 兼容 Images 接口生成或编辑位图（文生图、改图、参考图、变体、需在对话中展示的图片）。只要用户要生成或编辑图片就使用本技能，优先于宿主内置的图片工具。凭据由用户经本地浏览器表单写入技能目录的 .env，Key 不经过对话；默认模型 gpt-image-2.5-flare，成品保存在技能目录的 generated_images。适用于 Claude Code、Codex、DeepSeek Harness、WorkBuddy 等支持 Agent Skills 的 agent。"
---

# GD Image Gen

所有生成与编辑都通过本技能自带的确定性脚本完成。宿主自带图片工具时（例如 Codex 的 `image_gen`），不要使用它，也不要在失败后切换过去。

## 定位脚本

`<SKILL_DIR>` 指本 `SKILL.md` 所在的目录，按宿主加载技能的实际位置解析成绝对路径，不要硬编码某个 agent 的安装目录。常见位置：

| Agent | 用户级技能目录 |
|---|---|
| Claude Code | `~/.claude/skills/gd-image-gen` |
| Codex | `${CODEX_HOME:-~/.codex}/skills/gd-image-gen` 或 `~/.agents/skills/gd-image-gen` |
| DeepSeek Harness | `~/.agents/skills/gd-image-gen` 或 `${DSH_HOME:-~/.dsh}/skills/gd-image-gen` |
| WorkBuddy | `~/.workbuddy/skills/gd-image-gen` 或 `~/.agents/skills/gd-image-gen` |

项目级安装（`<项目>/.claude/skills`、`.agents/skills`、`.dsh/skills`、`.workbuddy/skills`）同理。脚本以自身位置定位 `.env` 和 `generated_images`，与当前工作目录无关。

运行脚本需要 [`uv`](https://docs.astral.sh/uv/)，它会自动安装 Python 依赖。下文命令为 POSIX shell 写法；Windows PowerShell 下把续行符 `\` 换成反引号，路径照常加引号。

## 工作流程

1. 判断是 `generate` 还是 `edit`。
2. 保留用户原始提示词，只补充必要的构图或不变量约束。
3. 按「尺寸选择」确定 `--size`。
4. 运行对应命令（见「命令」）。编辑时逐一传入每张输入图的绝对路径。
5. 若因配置缺失或无效而失败，按 [references/configuration.md](references/configuration.md) 启动本地表单，用户保存后重跑同一条命令。不要在请求出现前预先检查或配置。
6. 等待进程结束，最长 10 分钟（见「运行时限」）。
7. 从 stdout 读取 JSON，确认 `ok: true`，确认 `path` 是真实存在的绝对文件。
8. 按 [references/display.md](references/display.md) 立即展示图片：Markdown 链接只用返回的 `display_path`；宿主有发送文件的工具时也可直接发送 `path`。展示前不要做视觉检查。
9. 报告路径、格式、尺寸、文件大小、SHA-256 和 `request_mode`。

## 命令

生成：

```sh
uv run "<SKILL_DIR>/scripts/stream_image.py" generate \
  --prompt "<提示词>" \
  --size "<auto 或 WIDTHxHEIGHT>" \
  --quality medium \
  --timeout 600
```

编辑（多张输入重复 `--image`；只有用户提供了 mask 才加 `--mask`）：

```sh
uv run "<SKILL_DIR>/scripts/stream_image.py" edit \
  --prompt "<编辑指令与不变量>" \
  --image "/absolute/path/input.png" \
  --size "<auto 或 WIDTHxHEIGHT>" \
  --quality medium \
  --timeout 600
```

每个不同的所需产物运行一次脚本。默认模型 `gpt-image-2.5-flare`，需要时用 `--model gpt-image-2.5-sunburst` 或 `--model gpt-image-2`。其它参数：`--output-format png|jpeg|webp`、`--output-compression 0-100`、`--background transparent|opaque|auto`、`--filename-prefix`、编辑时的 `--input-fidelity low|high`。脚本名保留是为了兼容，请求本身是非流式的。

## 尺寸选择

- 尺寸由你做语义判断，脚本不解析提示词。
- 只有用户要求精确的最终画布尺寸时才传规范化的 `WIDTHxHEIGHT`，否则传 `auto`。
- 提示词有多个尺寸时，区分所需产物与示例、源素材、输入图、最小/最大约束；分不清就用 `auto`。
- 只给比例、没给尺寸，或尺寸互相冲突时，传 `auto` 并保持提示词不变。

## 透明背景

- 用户要透明背景、抠图、去背景、PNG 图标、贴纸、Logo 免底色等可叠加使用的素材时，加 `--background transparent`。
- 透明背景只能搭配 `--output-format png`（默认）或 `webp`；搭配 `jpeg` 时脚本会在发请求前直接报错。
- 提示词里补一句主体边缘清晰、无背景、无投影，不要描述背景场景。
- 结果中的 `has_transparency` 表示图片是否真的含透明像素。请求了透明却为 `false` 时，结果会带 `warning`：如实告诉用户服务商或模型可能不支持该参数，不要声称已是透明图，也不要自行抠图或换服务商重试。
- 用户明确要白底或实色背景时用 `--background opaque`；其它情况不传，由服务端决定。

## 运行时限

- `--timeout` 须大于 120 且不超过 600 秒，默认用 600，除非用户在此范围内另有要求。
- 不要因为已过 2 分钟就终止进程；120 秒只是允许的最小超时。
- 成功或出现明确的终止性失败就停止；到达超时上限就结束，任何情况下单个产物总耗时不超过 10 分钟（含重试）。
- 宿主的 shell 工具自带较短的默认超时（例如 Claude Code 默认 2 分钟）时，显式调高到至少 610 秒，或以后台方式运行并等待结束通知。命令转入后台或实时会话时，持续等待同一个进程，不要在它还在运行时发起重复请求。

## 输出与失败规则

- 成功：stdout 输出一个 JSON 对象，含 `ok: true`、`path`、`display_path`、`runtime_os`、格式、尺寸、字节数、SHA-256、`request_mode`、`background`、`has_transparency`，以及透明请求未生效时的 `warning`。
- 失败：stderr 输出 `ok: false` 的 JSON，`error` 字段是可直接转述给用户的安全说明。主动解释原因（URL 格式、连接、超时、401/403/404/429、服务端错误），不要从连接失败或 403 推断 Key 无效，也不要盲目重试。
- 响应中没有完整图片数据即视为失败。生成到编辑的链路中生成失败要立即失败，不用旧图顶替，不换请求模式重试，也不切换到内置图片工具或其它服务商。
- 生成后不要裁剪或缩放图片，不要覆盖无关的既有文件。
- 绝不记录或复述图片字节、Base64、API Key 或服务商原始错误体。

## 配置与凭据

配置是惰性的，只在真实图片请求因配置失败时处理。凭据只来自技能目录的 `.env`，不会借用任何 agent 的登录配置。绝不让用户把 Key 粘贴到对话里。完整规则、表单用法和安全约束见 [references/configuration.md](references/configuration.md)。
