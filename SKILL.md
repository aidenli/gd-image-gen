---
name: gd-image-gen
description: Generate or edit raster images through the Codex-configured OpenAI-compatible Images API using mandatory SSE streaming. Always use this skill for image generation and image editing in this Codex installation instead of the built-in image_gen tool, including long-running edits, reference-image work, variants, and requests that must be displayed in the conversation. Read the API key from Codex auth.json/auth.js, read the base URL from Codex config.toml, save completed images under CODEX_HOME/generated_images, and never treat partial stream events as final images.
---

# GD Image Gen

Use the bundled deterministic script for every generation or edit. Do not invoke the built-in `image_gen` tool and do not use `recover-codex-images`.

## Workflow

1. Decide whether the request is `generate` or `edit`.
2. Preserve the user's prompt and add only necessary composition or invariant constraints.
3. For edits, identify every input image explicitly and keep all file handles open while consuming the stream.
4. Run `scripts/stream_image.py` with `uv run`.
5. Keep the process running for up to 10 minutes. Do not terminate it merely because 2 minutes have elapsed; continue waiting unless it succeeds, returns an explicit failure, or reaches the 10-minute limit.
6. Treat only `image_generation.completed` or `image_edit.completed` as success. Never save a partial event as the final image.
7. Read the JSON result from stdout and verify `ok` is true.
8. Verify the returned `path` is an existing absolute file, then use only the returned `display_path` in the conversation. Never put raw `path` into Markdown and never construct a display path manually. The script determines `runtime_os` and emits an absolute display path with forward slashes.

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

9. Report the path, format, dimensions, file size, SHA-256, event count, and final event type.

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

## Authentication And Routing

- Read `OPENAI_API_KEY` from `$CODEX_HOME/auth.js` first, then `$CODEX_HOME/auth.json`. Only if neither provides a readable key, fall back to `.env` in the skill root (the parent of `scripts/`). Resolve `.env` relative to the script, not the working directory. Skip missing, unreadable, malformed, or keyless default files. An explicit `--auth-file` must succeed without fallback.
- If no API key can be read, direct the user to https://gdapi.xyz/keys and request a key, explaining that it will be persisted locally for future requests. Prefer hidden terminal input via `uv run scripts/stream_image.py save-key`. If the user supplies a key in conversation, pass it through process stdin to that command, never as a command-line argument or shell command literal.
- Persist the supplied key with `save-key` before retrying generation. This atomically updates `OPENAI_API_KEY` in the skill-root `.env`, preserving other settings and comments. Verify the command returns `ok: true`; a failed save must be reported, not described as remembered. This is local plaintext credential storage, not conversational memory.
- Never print or log the API key, include it in command-line arguments, or store it in tracked project files or memory notes. The git-ignored `.env` is the only additional permitted persistent copy. Do not write `$CODEX_HOME/gd-image-gen/auth.json` or change Codex login files.
- Read the active `model_provider` and its `base_url` from `$CODEX_HOME/config.toml`.
- Keep `stream=True`, `partial_images=1`, the request timeout within `(120, 600]` seconds, and SDK retries disabled.
- Never log image bytes or Base64. Log stream event types and aggregate counts only.

## Output And Failure Rules

- Save only completed images under `$CODEX_HOME/generated_images` using an atomic replacement.
- Strictly decode Base64, validate the complete image with Pillow, and calculate SHA-256 after writing.
- Do not crop or resize the completed image after generation.
- Fail if the stream closes without the matching completed event, even if partial images were received.
- Fail generation-to-edit workflows immediately when generation fails. Never substitute an older image.
- Do not make a non-streaming retry and do not switch to the built-in image tool after failure.
- Do not overwrite an unrelated existing file.
