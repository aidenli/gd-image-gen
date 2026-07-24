# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "openai>=2.38.0,<3",
#   "pillow>=10.0.0,<13",
# ]
# ///

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib
from typing import Any, Iterable


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "medium"
DEFAULT_OUTPUT_FORMAT = "png"
MIN_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_TIMEOUT_SECONDS = MAX_TIMEOUT_SECONDS
DEFAULT_PARTIAL_IMAGES = 1
API_SIZE_PATTERN = re.compile(r"(?:auto|[1-9][0-9]*x[1-9][0-9]*)\Z")
FORMAT_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
EXPECTED_COMPLETED_TYPES = {
    "generate": "image_generation.completed",
    "edit": "image_edit.completed",
}
PARTIAL_TYPES = {
    "generate": "image_generation.partial_image",
    "edit": "image_edit.partial_image",
}


class StreamImageError(RuntimeError):
    pass


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()


def _parse_auth_text(raw: str, source: Path) -> dict[str, Any]:
    text = raw.lstrip("\ufeff").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(
            r"\bOPENAI_API_KEY\b\s*[:=]\s*(['\"])(?P<value>[^'\"]+)\1",
            text,
        )
        if not match:
            raise StreamImageError(
                f"Could not parse OPENAI_API_KEY from Codex auth file: {source}"
            )
        return {"OPENAI_API_KEY": match.group("value")}
    if not isinstance(value, dict):
        raise StreamImageError(f"Codex auth file must contain an object: {source}")
    return value


def load_api_key(home: Path, explicit_auth: str | None = None) -> tuple[str, Path]:
    candidates = (
        [Path(explicit_auth).expanduser().resolve()]
        if explicit_auth
        else [home / "auth.js", home / "auth.json"]
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            auth = _parse_auth_text(path.read_text(encoding="utf-8"), path)
        except (OSError, UnicodeError) as exc:
            raise StreamImageError(f"Could not read Codex auth file: {path}") from exc
        key = auth.get("OPENAI_API_KEY")
        if isinstance(key, str) and key.strip():
            return key.strip(), path
        raise StreamImageError(f"OPENAI_API_KEY is missing from Codex auth file: {path}")
    names = ", ".join(str(path) for path in candidates)
    raise StreamImageError(f"Codex auth file not found; checked: {names}")


def load_base_url(home: Path, explicit_base_url: str | None = None) -> tuple[str, Path | None]:
    if explicit_base_url:
        return explicit_base_url.rstrip("/"), None
    config_path = home / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StreamImageError(f"Codex config file not found: {config_path}") from exc
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise StreamImageError(f"Could not parse Codex config file: {config_path}") from exc

    provider_name = config.get("model_provider")
    providers = config.get("model_providers")
    if not isinstance(provider_name, str) or not isinstance(providers, dict):
        raise StreamImageError("Codex config does not define model_provider/model_providers")
    provider = providers.get(provider_name)
    base_url = provider.get("base_url") if isinstance(provider, dict) else None
    if not isinstance(base_url, str) or not base_url.strip():
        raise StreamImageError(
            f"Codex model provider {provider_name!r} does not define base_url"
        )
    return base_url.rstrip("/"), config_path


def read_prompt(prompt: str | None, prompt_file: str | None) -> str:
    if prompt and prompt_file:
        raise StreamImageError("Use --prompt or --prompt-file, not both")
    if prompt_file:
        path = Path(prompt_file).expanduser().resolve()
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise StreamImageError(f"Could not read prompt file: {path}") from exc
    else:
        value = (prompt or "").strip()
    if not value:
        raise StreamImageError("Prompt is empty")
    return value


def existing_file(raw: str, label: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise StreamImageError(f"{label} file not found: {path}")
    return path


def event_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        value = event.model_dump(mode="json", exclude_none=True)
    elif isinstance(event, dict):
        value = event
    else:
        value = {
            "type": getattr(event, "type", type(event).__name__),
            "b64_json": getattr(event, "b64_json", None),
        }
    return value if isinstance(value, dict) else {}


def consume_stream(stream: Iterable[Any], operation: str) -> tuple[str, dict[str, Any]]:
    expected_completed = EXPECTED_COMPLETED_TYPES[operation]
    expected_partial = PARTIAL_TYPES[operation]
    completed_b64: str | None = None
    event_count = 0
    partial_count = 0
    last_event_type: str | None = None
    try:
        for event in stream:
            event_count += 1
            data = event_dict(event)
            event_type = str(data.get("type") or type(event).__name__)
            last_event_type = event_type
            if event_type == expected_partial:
                partial_count += 1
            elif event_type == expected_completed:
                encoded = data.get("b64_json")
                if not isinstance(encoded, str) or not encoded:
                    raise StreamImageError(f"{expected_completed} did not contain b64_json")
                completed_b64 = encoded
            print(
                json.dumps(
                    {
                        "stream_event": event_count,
                        "type": event_type,
                        "has_image": isinstance(data.get("b64_json"), str),
                    },
                    ensure_ascii=True,
                ),
                file=sys.stderr,
            )
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()

    if completed_b64 is None:
        raise StreamImageError(
            f"Stream ended without {expected_completed}; events={event_count}, "
            f"partials={partial_count}, last={last_event_type or 'none'}"
        )
    return completed_b64, {
        "event_count": event_count,
        "partial_event_count": partial_count,
        "completed_event_type": expected_completed,
        "last_event_type": last_event_type,
    }


def decode_and_validate_image(encoded: str, requested_format: str) -> tuple[bytes, str, int, int]:
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise StreamImageError("Completed event contained invalid Base64 image data") from exc
    if not data:
        raise StreamImageError("Completed event contained an empty image")

    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            detected_format = str(image.format or "").upper()
            width, height = image.size
            image.load()
    except Exception as exc:
        raise StreamImageError("Completed event did not contain a valid complete image") from exc

    expected = "JPEG" if requested_format == "jpeg" else requested_format.upper()
    if detected_format != expected:
        raise StreamImageError(
            f"Image format mismatch: requested {expected}, received {detected_format or 'unknown'}"
        )
    if width <= 0 or height <= 0:
        raise StreamImageError("Completed image dimensions are invalid")
    return data, detected_format, width, height


def sanitize_prefix(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return normalized or "generated"


def runtime_os() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if os.name == "posix":
        return "linux"
    raise StreamImageError(f"Unsupported operating system for image display: {os.name}")


def display_path(path: Path) -> str:
    absolute_path = path.resolve()
    rendered = absolute_path.as_posix()
    operating_system = runtime_os()
    if operating_system == "windows":
        if not re.fullmatch(r"[A-Za-z]:/.*", rendered):
            raise StreamImageError(f"Unsupported Windows image path: {absolute_path}")
    elif not rendered.startswith("/"):
        raise StreamImageError(f"Unsupported POSIX image path: {absolute_path}")
    return rendered


def parse_api_size(raw: str) -> str:
    value = raw.strip().lower()
    if not API_SIZE_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("--size must be auto or WIDTHxHEIGHT")
    return value


def save_image(
    data: bytes,
    image_format: str,
    output_dir: Path,
    filename_prefix: str,
) -> tuple[Path, str]:
    extension = FORMAT_EXTENSIONS.get(image_format)
    if extension is None:
        raise StreamImageError(f"Unsupported completed image format: {image_format}")
    digest = hashlib.sha256(data).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    prefix = sanitize_prefix(filename_prefix)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = (output_dir / f"{prefix}-{stamp}-{digest[:12]}.{extension}").resolve()

    if destination.exists():
        if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
            return destination, digest
        raise StreamImageError(f"Refusing to overwrite existing output: {destination}")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output_dir, prefix=".gd-image-gen-", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    written_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if written_digest != digest:
        destination.unlink(missing_ok=True)
        raise StreamImageError("Saved image SHA-256 does not match the completed event")
    return destination, digest


def request_payload(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "n": 1,
        "size": args.api_size,
        "quality": args.quality,
        "output_format": args.output_format,
        "stream": True,
        "partial_images": DEFAULT_PARTIAL_IMAGES,
    }
    if args.background is not None:
        payload["background"] = args.background
    if args.output_compression is not None:
        payload["output_compression"] = args.output_compression
    if args.command == "edit" and args.input_fidelity is not None:
        payload["input_fidelity"] = args.input_fidelity
    return payload


def dry_run_result(
    args: argparse.Namespace,
    home: Path,
    auth_path: Path,
    config_path: Path | None,
    base_url: str,
    prompt: str,
) -> dict[str, Any]:
    endpoint = "/images/generations" if args.command == "generate" else "/images/edits"
    return {
        "ok": True,
        "dry_run": True,
        "operation": args.command,
        "auth_file": str(auth_path),
        "config_file": str(config_path) if config_path else None,
        "request_url": base_url + endpoint,
        "prompt_length": len(prompt),
        "stream": True,
        "partial_images": DEFAULT_PARTIAL_IMAGES,
        "api_size": args.api_size,
        "output_dir": str((home / "generated_images").resolve()),
        "images": [str(path) for path in getattr(args, "image_paths", [])],
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    home = codex_home()
    api_key, auth_path = load_api_key(home, args.auth_file)
    base_url, config_path = load_base_url(home, args.base_url)
    prompt = read_prompt(args.prompt, args.prompt_file)

    if args.command == "edit":
        args.image_paths = [existing_file(raw, "Input image") for raw in args.image]
        args.mask_path = existing_file(args.mask, "Mask") if args.mask else None
    else:
        args.image_paths = []
        args.mask_path = None

    if args.dry_run:
        return dry_run_result(args, home, auth_path, config_path, base_url, prompt)

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise StreamImageError("The openai package is unavailable; run this script with uv run") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=args.timeout,
        max_retries=0,
    )
    payload = request_payload(args, prompt)
    try:
        if args.command == "generate":
            stream = client.images.generate(**payload)
            completed_b64, stream_metrics = consume_stream(stream, "generate")
        else:
            with ExitStack() as stack:
                image_files = [stack.enter_context(path.open("rb")) for path in args.image_paths]
                request = dict(payload)
                request["image"] = image_files if len(image_files) > 1 else image_files[0]
                if args.mask_path is not None:
                    request["mask"] = stack.enter_context(args.mask_path.open("rb"))
                stream = client.images.edit(**request)
                completed_b64, stream_metrics = consume_stream(stream, "edit")
    finally:
        client.close()

    data, image_format, width, height = decode_and_validate_image(
        completed_b64, args.output_format
    )
    output_dir = home / "generated_images"
    prefix = args.filename_prefix or ("generated" if args.command == "generate" else "edited")
    path, digest = save_image(data, image_format, output_dir, prefix)
    return {
        "ok": True,
        "operation": args.command,
        "path": str(path),
        "display_path": display_path(path),
        "runtime_os": runtime_os(),
        "format": image_format,
        "width": width,
        "height": height,
        "api_size": args.api_size,
        "size": path.stat().st_size,
        "sha256": digest,
        **stream_metrics,
    }


def add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--quality", choices=["low", "medium", "high", "auto"], default=DEFAULT_QUALITY
    )
    parser.add_argument(
        "--output-format",
        choices=["png", "jpeg", "webp"],
        default=DEFAULT_OUTPUT_FORMAT,
    )
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--background", choices=["transparent", "opaque", "auto"])
    parser.add_argument("--filename-prefix")
    parser.add_argument("--size", type=parse_api_size, default=DEFAULT_SIZE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--auth-file", help=argparse.SUPPRESS)
    parser.add_argument("--base-url", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or edit an image through the Codex-configured streaming Images API"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    add_shared_arguments(generate)

    edit = subparsers.add_parser("edit")
    add_shared_arguments(edit)
    edit.add_argument("--image", action="append", required=True)
    edit.add_argument("--mask")
    edit.add_argument("--input-fidelity", choices=["low", "high"])
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not MIN_TIMEOUT_SECONDS < args.timeout <= MAX_TIMEOUT_SECONDS:
        raise StreamImageError(
            "--timeout must be greater than 120 and no greater than 600 seconds"
        )
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        raise StreamImageError("--output-compression must be between 0 and 100")
    args.api_size = args.size


def safe_error(exc: Exception) -> dict[str, Any]:
    status_code = getattr(exc, "status_code", None)
    return {
        "ok": False,
        "error_type": type(exc).__name__,
        "status_code": status_code if isinstance(status_code, int) else None,
        "error": str(exc) if isinstance(exc, StreamImageError) else "Streaming image request failed",
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
        result = execute(args)
    except Exception as exc:
        print(json.dumps(safe_error(exc), ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
