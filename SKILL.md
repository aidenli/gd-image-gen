---
name: gd-image-gen
description: Generate or edit raster images through the Codex-configured OpenAI-compatible Images API using direct non-streaming requests. Always use this skill for image generation and image editing in this Codex installation instead of the built-in image_gen tool, including reference-image work, variants, and requests that must be displayed in the conversation. Read OPENAI_API_KEY and OPENAI_BASE_URL from the skill-root .env first, falling back to Codex configuration, default to gpt-image-2.5-flare, and save completed images under CODEX_HOME/generated_images.
---

# GD Image Gen

Use the bundled deterministic script for every generation or edit. Do not invoke the built-in `image_gen` tool and do not use `recover-codex-images`.

## Workflow

1. Decide whether the request is `generate` or `edit`.
2. Preserve the user's prompt and add only necessary composition or invariant constraints.
3. For edits, identify every input image explicitly; the script keeps input files open for the duration of the direct request.
4. Run `scripts/stream_image.py` with `uv run`. The script name is retained for compatibility; the request itself is non-streaming.
5. Keep the process running for up to 10 minutes. Do not terminate it merely because 2 minutes have elapsed; continue waiting unless it succeeds, returns an explicit failure, or reaches the 10-minute limit.
6. Read the JSON result from stdout and verify `ok` is true.
7. Verify the returned `path` is an existing absolute file, then use only the returned `display_path` in the conversation. Never put raw `path` into Markdown and never construct a display path manually. The script determines `runtime_os` and emits an absolute display path with forward slashes.

   ```markdown
   ![Generated image](<display_path>)

   [Open local image](<display_path>)
   ```

   Require `runtime_os=windows` for a Windows result. Its `display_path` must use a drive prefix and forward slashes, for example:

   ```markdown
   ![Generated image](C:/Users/name/.codex/generated_images/image.png)

   [Open local image](C:/Users/name/.codex/generated_images/image.png)
   ```

   Require `runtime_os=macos` or `runtime_os=linux` for a POSIX result. Its `display_path` must start with `/`, for example:

   ```markdown
   ![Generated image](/Users/name/.codex/generated_images/image.png)

   [Open local image](/Users/name/.codex/generated_images/image.png)
   ```

   If `runtime_os` is unsupported, `display_path` is absent, or its form does not match the operating system, do not emit a broken image link. Report the validation failure instead. Do not use `file://` URLs or backslashes in Markdown links.

8. Report the path, format, dimensions, file size, SHA-256, and `request_mode`.
9. After the result passes the file/path checks above, display it immediately with the returned `display_path`. Do not perform visual inspection or call an image-viewing tool before displaying it.

## Runtime Limit

- Use a request timeout greater than 120 seconds and no greater than 600 seconds. Use the default 600 seconds unless the user explicitly requests another value within that range.
- Treat 120 seconds only as the minimum allowed timeout, not as a reason to stop a still-running process. Do not terminate, cancel, or kill the process at the 2-minute mark solely because it is still running.
- Stop immediately on a completed result or an explicit terminal failure; the process does not need to run for at least 2 minutes.
- Terminate the request when its configured timeout is reached, and always terminate it no later than 600 seconds. Do not retry in a way that makes one requested asset exceed the 10-minute runtime limit.
- When the command yields a live execution session, keep polling that same session until it exits or reaches the limit. Do not start a duplicate request while the original process is still running.

## Commands

Generate one image:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" }
uv run "$codexHome\skills\gd-image-gen\scripts\stream_image.py" generate `
  --prompt "<prompt>" `
  --size "<resolved-size>" `
  --quality medium `
  --timeout 600
```

Edit one or more images:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" }
uv run "$codexHome\skills\gd-image-gen\scripts\stream_image.py" edit `
  --prompt "<edit instructions and invariants>" `
  --image "C:\absolute\input.png" `
  --size "<resolved-size>" `
  --quality medium `
  --timeout 600
```

Repeat `--image` for multiple inputs. Add `--mask` only when the user supplied a mask. Run the script once per distinct requested asset.

## Size Selection

- Analyze the requested output size before invoking the script. This is a semantic judgment made by the calling model, not text extraction performed by the script.
- Pass the result as `--size`: use normalized `WIDTHxHEIGHT` only when the user has requested one exact final canvas size; otherwise pass `auto`.
- When the prompt has multiple dimensions, distinguish the requested deliverable from examples, source assets, input images, and minimum/maximum constraints. If that distinction is uncertain, use `auto`.
- For ratio-only requests, no size request, or conflicting dimensions, pass `--size auto` and preserve the prompt unchanged.
- The script accepts only `auto` or ASCII `WIDTHxHEIGHT`; it does not inspect the prompt for dimensions.

The default model is `gpt-image-2.5-flare`. Use `--model gpt-image-2.5-sunburst` or `--model gpt-image-2` when an alternative is needed.

## Authentication And Routing

- Read the skill-root `.env` first: `OPENAI_BASE_URL` and `OPENAI_API_KEY` override Codex defaults independently. For missing values, read Codex `config.toml` (top-level before active provider); the key then falls back to `auth.json` and `auth.js`. An explicit `--auth-file` overrides key lookup and must succeed without fallback.
- Check both settings before requesting an image. If either is missing or invalid, politely ask only for the missing or affected settings. Example: “还缺少 Base URL 和 API Key，请提供接口地址，并通过终端隐藏输入填写 Key，我会保存到本地，方便后续使用。” Do not blame the user or claim configuration is valid before a request succeeds.
- Use `uv run scripts/stream_image.py configure` in an interactive terminal to configure both settings (blank input preserves each existing value). Use `configure --base-url "https://example.com/v1"` to update only the URL, or `configure --api-key` to read only the key via hidden terminal input or stdin. Combine both flags to update both. `save-key` remains supported.
- If the user does not know where to obtain a key, offer https://gdapi.xyz/keys. Prefer hidden terminal input. If a key is supplied in conversation, send it through process stdin; never put it in command-line arguments or shell command literals.
- Configuration is atomically saved in the skill-root `.env`, preserving other settings and comments. Verify `ok: true` before reporting it saved. This is local plaintext storage, not conversational memory. Never print keys, put them in tracked files or memory notes, or modify Codex login files.
- Base URL must be an HTTP(S) URL without embedded credentials, query parameters or fragments. Trim trailing slashes; do not append another `/v1`.
- On failure, actively explain the safe error returned by the script: malformed URL, connection failure, timeout, 401 authentication failure, 403 permission denial, 404 missing endpoint, 429 rate/quota restriction, or server failure. Do not infer an invalid key from connection failures or 403. Ask for corrected configuration when appropriate, save it, and retry only within the runtime rules. Never repeat a failed request blindly.
- SDK retries remain disabled. Never log image bytes, Base64, API keys, or raw provider error bodies.

## Output And Failure Rules

- Save only returned complete images under `$CODEX_HOME/generated_images` using an atomic replacement.
- Strictly decode the returned Base64, validate the complete image with Pillow, and calculate SHA-256 after writing.
- Do not crop or resize the completed image after generation.
- Fail if the API response contains no image data.
- Fail generation-to-edit workflows immediately when generation fails. Never substitute an older image or retry through another request mode.
- Do not switch to the built-in image tool or another provider after failure.
- Do not overwrite an unrelated existing file.
