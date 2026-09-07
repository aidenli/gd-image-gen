# GD Image Gen

Generate and edit raster images through the Codex-configured, OpenAI-compatible Images API with Server-Sent Events (SSE).

The skill accepts only completed stream events as image output. It writes validated images to `$CODEX_HOME/generated_images` and returns a display-safe absolute path for the current operating system.

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

## Requirements

- Install [`uv`](https://docs.astral.sh/uv/).
- Configure a Codex API key in `$CODEX_HOME/auth.json` or `$CODEX_HOME/auth.js`.
- Configure the active model provider and its `base_url` in `$CODEX_HOME/config.toml`.

The script checks `$CODEX_HOME/auth.js`, then `$CODEX_HOME/auth.json`. Only when neither provides a readable key does it fall back to `.env` in the skill root (the parent of `scripts/`, regardless of working directory). To persist a fallback key, run `uv run scripts/stream_image.py save-key` and enter it at the hidden prompt (automation can use stdin). The command atomically updates `OPENAI_API_KEY` in this git-ignored `.env`, preserving other settings and comments; future requests reuse it as a fallback. Credentials are stored as local plaintext. Do not put an API key in a command line, prompt file, or tracked repository file.

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
- Stop on a completed event, an explicit terminal failure, or the configured timeout.
- Do not retry in a way that lets one requested asset exceed 10 minutes of runtime.

## Result Contract

On success, stdout contains one JSON object with `ok: true`, output metadata, SHA-256, stream event counts, and `display_path`. Use `display_path` when rendering a local image in a Codex response.

On failure, stderr contains one JSON object with `ok: false`. A stream without `image_generation.completed` or `image_edit.completed` is a failure, even when it includes partial images.

See [SKILL.md](SKILL.md) for the complete agent workflow and constraints.
