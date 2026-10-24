---
name: gd-image-gen
description: 通过用户自备的 OpenAI 兼容 Images 接口，以非流式请求生成或编辑位图。在本 Codex 安装中，所有图片生成与图片编辑都必须使用本技能，不要使用内置的 image_gen 工具，包括参考图作业、变体和需要在对话中显示的请求。需要用户自行在技能根目录的 .env 中提供 OPENAI_BASE_URL 与 OPENAI_API_KEY（不回退到 Codex 配置）；通过本地浏览器表单收集，确保 Key 不经过对话。默认模型为 gpt-image-2.5-flare，成品图保存到本技能根目录下的 generated_images。
---

# GD Image Gen

每次生成或编辑都使用本技能自带的确定性脚本。不要调用内置的 `image_gen` 工具，也不要用 `recover-codex-images`。

## 工作流程

1. 判断请求属于 `generate` 还是 `edit`。
2. 保留用户的原始提示词，只补充必要的构图或不变量约束。
3. 编辑时逐一明确每张输入图；脚本会在整个直连请求期间保持这些文件打开。
4. 用 `uv run` 运行 `scripts/stream_image.py`。脚本名保留是为了兼容，请求本身是非流式的。
5. 如果因为配置缺失或无效而失败，按「何时配置」一节处理：启动表单，让用户填写，然后重新执行同一条命令。不要在请求出现之前预先检查配置。
6. 保持进程运行，最长 10 分钟。不要仅因为已过 2 分钟就终止；除非它成功、返回明确失败，或到达 10 分钟上限，否则继续等待。
7. 从 stdout 读取 JSON 结果，确认 `ok` 为 true。
8. 确认返回的 `path` 是真实存在的绝对文件，然后只使用返回的 `display_path` 在对话中展示。绝不把原始 `path` 放进 Markdown，也绝不手工拼接展示路径。脚本会判断 `runtime_os` 并输出使用正斜杠的绝对展示路径。

   ```markdown
   ![生成的图片](<display_path>)

   [打开本地图片](<display_path>)
   ```

   Windows 结果必须满足 `runtime_os=windows`，其 `display_path` 需带盘符并使用正斜杠，例如：

   ```markdown
   ![生成的图片](C:/Users/name/.codex/skills/gd-image-gen/generated_images/image.png)

   [打开本地图片](C:/Users/name/.codex/skills/gd-image-gen/generated_images/image.png)
   ```

   POSIX 结果必须满足 `runtime_os=macos` 或 `runtime_os=linux`，其 `display_path` 需以 `/` 开头，例如：

   ```markdown
   ![生成的图片](/Users/name/.codex/skills/gd-image-gen/generated_images/image.png)

   [打开本地图片](/Users/name/.codex/skills/gd-image-gen/generated_images/image.png)
   ```

   如果 `runtime_os` 不受支持、`display_path` 缺失，或它的形式与操作系统不符，就不要输出会坏掉的图片链接，改为报告校验失败。Markdown 链接中不要使用 `file://` URL 或反斜杠。

9. 报告路径、格式、尺寸、文件大小、SHA-256 和 `request_mode`。
10. 结果通过上述文件与路径检查后，立即用返回的 `display_path` 展示。展示之前不要做视觉检查，也不要调用看图工具。

## 运行时限

- 请求超时需大于 120 秒且不超过 600 秒。除非用户在该范围内明确要求其它值，否则用默认的 600 秒。
- 120 秒只是允许的最小超时，不是停止仍在运行的进程的理由。不要仅仅因为到了 2 分钟就终止、取消或杀掉进程。
- 结果完成或出现明确的终止性失败时立即停止；进程不需要至少运行 2 分钟。
- 到达配置的超时时间就终止请求，并且无论如何不得晚于 600 秒。重试方式不得让单个所需产物超过 10 分钟总运行时间。
- 当命令产生实时执行会话时，持续轮询同一个会话，直到它退出或到达上限。原进程仍在运行时不要启动重复请求。

## 命令

生成一张图片：

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" }
uv run "$codexHome\skills\gd-image-gen\scripts\stream_image.py" generate `
  --prompt "<提示词>" `
  --size "<解析后的尺寸>" `
  --quality medium `
  --timeout 600
```

编辑一张或多张图片：

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" }
uv run "$codexHome\skills\gd-image-gen\scripts\stream_image.py" edit `
  --prompt "<编辑指令与不变量>" `
  --image "C:\absolute\input.png" `
  --size "<解析后的尺寸>" `
  --quality medium `
  --timeout 600
```

多张输入就重复 `--image`。只有用户提供了 mask 时才加 `--mask`。每个不同的所需产物运行一次脚本。

## 尺寸选择

- 调用脚本前先分析请求的输出尺寸。这是调用方模型的语义判断，不是脚本做的文本提取。
- 把判断结果作为 `--size` 传入：只有当用户要求一个精确的最终画布尺寸时，才用规范化的 `WIDTHxHEIGHT`；否则传 `auto`。
- 当提示词中出现多个尺寸时，要区分「所需产物」与示例、源素材、输入图、最小/最大约束。若无法确定这种区分，用 `auto`。
- 只给比例、没提尺寸，或尺寸互相冲突时，传 `--size auto` 并保持提示词不变。
- 脚本只接受 `auto` 或 ASCII 的 `WIDTHxHEIGHT`；它不会去提示词里解析尺寸。

默认模型为 `gpt-image-2.5-flare`。需要其它模型时用 `--model gpt-image-2.5-sunburst` 或 `--model gpt-image-2`。

## 何时配置

配置是惰性的：等到真有图片请求需要它时才配置。不要提前做。

只有以下条件同时成立时才触发配置表单：

1. 用户此刻确实要求生成或编辑图片 —— 不是在问本技能做什么，不是在问怎么配置，也不是让你安装、更新或验证技能。
2. `status`（或失败的 `generate`/`edit`）显示 `ready: false`，或该请求刚刚因配置错误失败。

绝不要做以下任何事：

- 不要在安装、升级、自检或「装好了吗」这类任务中运行 `configure`、`configure --web` 或 `save-key`。
- 不要仅因为技能被提及、被加载或文档被读取就启动表单。
- 不要以「先准备好，稍后用」为由提前启动表单，也不要为用户尚未确认的请求启动它。
- 不要同时开第二个表单：如果已有一个在等待输入，让用户去看那个页面，而不是再开一个。

真有请求到来时推荐的做法：直接运行 `generate`/`edit`（不确定时先跑 `status`）。让缺配置的错误自然浮现，用一句话说明缺什么，然后启动表单。这样用户始终掌握主动权，也不会突然弹出浏览器窗口。

表单保存成功后，重新运行 `status`，确认 `ready: true`，然后继续原来的请求，不要让用户重复描述需求。

## 鉴权与路由

- 配置只来自用户。技能根目录的 `.env` 保存 `OPENAI_BASE_URL` 与 `OPENAI_API_KEY`；**不会**回退到 Codex 的 `config.toml`、`auth.json` 或 `auth.js`。永远不要假设凭据已存在，也不要从 Codex 登录文件中复制。
- 在真有请求需要时运行 `status`。它报告 `ready`、已保存的 Base URL，以及形如 `sk-********abcd` 的掩码 Key，绝不打印完整 Key。`status` 是只读的，除非用户要求，否则不要把它当作安装或健康检查的一部分来运行。
- 当配置缺失或无效、**且确实有图片请求**时（见「何时配置」），不要要求用户把 Key 粘贴到对话里，改为启动本地浏览器表单：

  ```powershell
  $codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" }
  uv run "$codexHome\skills\gd-image-gen\scripts\stream_image.py" configure --web
  ```

  脚本在 `127.0.0.1` 上以随机端口和一次性令牌提供表单，直接写入本地 `.env`，保存后退出。对用户说：「请在刚打开的本地页面填写 Base URL 和 API Key，保存后我会继续。」如果浏览器没有自动打开，把打印出的地址给用户。若 `webbrowser` 完全无法打开，加 `--no-browser` 并把地址交给用户。
- 用户填写期间保持该表单进程运行（默认 300 秒，可用 `--timeout` 调整）。不要启动第二个表单。若超时，说明没有保存任何内容，并询问是否重新启动。
- 保存后重新运行 `status`，确认 `ready` 为 true，再开始生成。
- 只有用户坚持用其它方式提供 Key 时，才用 `configure --api-key` 从进程 stdin 读取（有 TTY 时用隐藏输入）。绝不把 Key 放进命令行参数、shell 字面量、你写的文件或受版本控制的文件。如果 Key 已经出现在对话中，只通过 stdin 保存，并告知用户它已进入对话记录，建议轮换。
- `configure --base-url "https://example.com/v1"` 只更新 URL。`save-key` 仍支持从 stdin 读 Key。配置以原子方式保存到技能根目录的 `.env`，保留其它设置和注释；报告成功前先确认 `ok: true`。这是本地明文存储。
- Base URL 必须是 HTTP(S) 地址，不含内嵌凭据、查询参数或片段。只去掉末尾斜杠，不要再补一个 `/v1`。
- 失败时主动解释脚本返回的安全错误：URL 格式错误、连接失败、超时、401 认证失败、403 权限拒绝、404 接口不存在、429 限流或额度限制、服务端错误。不要从连接失败或 403 推断 Key 无效。适当时请用户修正配置，保存后仅在运行规则允许的范围内重试。绝不盲目重复失败的请求。
- SDK 重试保持关闭。绝不记录图片字节、Base64、API Key 或服务商原始错误体。

## 输出与失败规则

- 只把返回的完整图片保存到本技能根目录下的 `generated_images`，并使用原子替换。
- 严格解码返回的 Base64，用 Pillow 校验图片完整，写入后计算 SHA-256。
- 生成完成后不要裁剪或缩放图片。
- API 响应中没有图片数据即视为失败。
- 生成到编辑的流程中，生成失败要立即失败。绝不用旧图顶替，也不换另一种请求模式重试。
- 失败后不要切换到内置图片工具或其它服务商。
- 不要覆盖无关的既有文件。
