# GD Image Gen

Generate and edit raster images through the Codex-configured, OpenAI-compatible Images API with direct non-streaming requests.

The skill accepts only complete, validated image data as output. It writes validated images to `$CODEX_HOME/generated_images` and returns a display-safe absolute path for the current operating system.

## Install

Install the repository as a Codex skill:

```sh
git clone https://github.com/aidenli/gd-image-gen.git "${CODEX_HOME:-$HOME/.codex}/skills/gd-image-gen"
```

Update an existing installation:

```sh
git -C "${CODEX_HOME:-$HOME/.codex}/skills/gd-image-gen" pull --ff-only
```

Codex discovers the skill from `SKILL.md`. Its UI metadata is in `agents/openai.yaml`.

## Requirements and configuration

Install [`uv`](https://docs.astral.sh/uv/), then configure the skill:

```sh
uv run scripts/stream_image.py configure
```

The interactive command asks for Base URL and a hidden API Key; blank input preserves an existing value. To update individual settings:

```sh
uv run scripts/stream_image.py configure --base-url "https://example.com/v1"
uv run scripts/stream_image.py configure --api-key
```

Combine both flags to save both settings; automation sends the key through stdin, never a command-line value. `save-key` remains compatible. Settings are atomically saved as `OPENAI_BASE_URL` and `OPENAI_API_KEY` in the skill-root, git-ignored `.env`, preserving other settings and comments. Keys are stored as local plaintext. See `.env.example` for manual configuration.

Each `.env` setting overrides Codex defaults independently. Missing values fall back to `$CODEX_HOME/config.toml` (top-level before active provider); keys then fall back to `auth.json` and `auth.js`. Explicit `--auth-file` overrides key lookup without fallback. Process environment variables are not configuration sources for these two settings.

Missing or invalid settings are reported together before any API request. URLs must use HTTP(S) and contain no embedded credentials, query parameters or fragments. Errors distinguish connection failures, timeouts, authentication, permission, endpoint, rate/quota and server failures without exposing raw provider messages or keys. Configuration saving validates format; it does not prove connectivity or key validity.

The default model is `gpt-image-2.5-flare`; use `gpt-image-2.5-sunburst` or `gpt-image-2` with `--model` when needed.

## Use

Run one command for each requested image. `uv run` installs the script's Python dependencies when needed.

```sh
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
uv run "$CODEX_HOME/skills/gd-image-gen/scripts/stream_image.py" generate \
  --prompt "A red paper lantern on a wooden table, studio product photo" \
  --size auto \
  --quality medium \
  --timeout 600
```

Edit an image by passing every input explicitly:

```sh
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
uv run "$CODEX_HOME/skills/gd-image-gen/scripts/stream_image.py" edit \
  --prompt "Replace the background with a quiet city street at dawn; preserve the subject" \
  --image /absolute/path/to/input.png \
  --size auto \
  --quality medium \
  --timeout 600
```

Use `--mask /absolute/path/to/mask.png` only when a mask is supplied. Repeat `--image` for multiple source images.

## Runtime Rules

- Use a timeout greater than 120 seconds and no greater than 600 seconds.
- Do not terminate a running request solely because it has passed 2 minutes.
- Stop on a completed result, an explicit terminal failure, or the configured timeout.
- Do not retry in a way that lets one requested asset exceed 10 minutes of runtime.

## Result Contract

On success, stdout contains one JSON object with `ok: true`, output metadata, SHA-256, request mode, and `display_path`. Use `display_path` when rendering a local image in a Codex response.

On failure, stderr contains one JSON object with `ok: false`. A response without complete image data is a failure.

See [SKILL.md](SKILL.md) for the complete agent workflow and constraints.
