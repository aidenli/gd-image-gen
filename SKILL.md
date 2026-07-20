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
5. Treat only `image_generation.completed` or `image_edit.completed` as success. Never save a partial event as the final image.
6. Read the JSON result from stdout and verify `ok` is true.
7. Display the returned absolute `path` in the conversation using both forms. Prefer the cross-platform Markdown path format: keep the path absolute, convert backslashes to forward slashes, and do not wrap the image URL in angle brackets. Use the same normalized path for the image and the local-open link.

   ```markdown
   ![Generated image](/absolute/path/generated-image.png)

   [Open local image](/absolute/path/generated-image.png)
   ```

   On Windows, convert `C:\Users\name\.codex\generated_images\image.png` to:

   ```markdown
   ![Generated image](C:/Users/name/.codex/generated_images/image.png)

   [Open local image](C:/Users/name/.codex/generated_images/image.png)
   ```

   On macOS or Linux, use the absolute POSIX path directly:

   ```markdown
   ![Generated image](/Users/name/.codex/generated_images/image.png)

   [Open local image](/Users/name/.codex/generated_images/image.png)
   ```

8. Report the path, format, dimensions, file size, SHA-256, event count, and final event type.

## Commands

Generate one image:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" }
uv run "$codexHome\skills\gd-image-gen\scripts\stream_image.py" generate `
  --prompt "<prompt>" `
  --size "<resolved-size>" `
  --quality medium
```

Edit one or more images:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { "$HOME\.codex" }
uv run "$codexHome\skills\gd-image-gen\scripts\stream_image.py" edit `
  --prompt "<edit instructions and invariants>" `
  --image "C:\absolute\input.png" `
  --size "<resolved-size>" `
  --quality medium
```

Repeat `--image` for multiple inputs. Add `--mask` only when the user supplied a mask. Run the script once per distinct requested asset.

## Size Selection

- Analyze the requested output size before invoking the script. This is a semantic judgment made by the calling model, not text extraction performed by the script.
- Pass the result as `--size`: use normalized `WIDTHxHEIGHT` only when the user has requested one exact final canvas size; otherwise pass `auto`.
- When the prompt has multiple dimensions, distinguish the requested deliverable from examples, source assets, input images, and minimum/maximum constraints. If that distinction is uncertain, use `auto`.
- For ratio-only requests, no size request, or conflicting dimensions, pass `--size auto` and preserve the prompt unchanged.
- The script accepts only `auto` or ASCII `WIDTHxHEIGHT`; it does not inspect the prompt for dimensions.

## Authentication And Routing

- Read `OPENAI_API_KEY` from `$CODEX_HOME/auth.json`; also accept `$CODEX_HOME/auth.js` when it contains JSON or a literal `OPENAI_API_KEY` assignment.
- Never print, log, pass on the command line, or copy the API key into another file.
- Read the active `model_provider` and its `base_url` from `$CODEX_HOME/config.toml`.
- Keep `stream=True`, `partial_images=1`, a long request timeout, and SDK retries disabled.
- Never log image bytes or Base64. Log stream event types and aggregate counts only.

## Output And Failure Rules

- Save only completed images under `$CODEX_HOME/generated_images` using an atomic replacement.
- Strictly decode Base64, validate the complete image with Pillow, and calculate SHA-256 after writing.
- Do not crop or resize the completed image after generation.
- Fail if the stream closes without the matching completed event, even if partial images were received.
- Fail generation-to-edit workflows immediately when generation fails. Never substitute an older image.
- Do not make a non-streaming retry and do not switch to the built-in image tool after failure.
- Do not overwrite an unrelated existing file.
