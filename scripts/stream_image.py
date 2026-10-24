# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "openai>=2.38.0,<3",
#   "pillow>=10.0.0,<13",
#   "python-dotenv>=1.0,<2",
# ]
# ///

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
import webbrowser

from dotenv import dotenv_values, set_key


DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "medium"
DEFAULT_OUTPUT_FORMAT = "png"
MIN_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_TIMEOUT_SECONDS = MAX_TIMEOUT_SECONDS
SKILL_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_ROOT / ".env"
OUTPUT_DIR = SKILL_ROOT / "generated_images"
API_SIZE_PATTERN = re.compile(r"(?:auto|[1-9][0-9]*x[1-9][0-9]*)\Z")
FORMAT_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


class StreamImageError(RuntimeError):
    pass


def read_env() -> dict[str, Any]:
    if not ENV_PATH.exists():
        return {}
    try:
        return dotenv_values(ENV_PATH, interpolate=False)
    except (OSError, UnicodeError) as exc:
        raise StreamImageError(f"无法读取配置文件：{ENV_PATH}") from exc


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        parsed.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError as exc:
        raise StreamImageError("Base URL 格式不正确，请提供完整的 http:// 或 https:// 接口地址。") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or any(c.isspace() or not c.isprintable() for c in value)):
        raise StreamImageError("Base URL 格式不正确，请提供完整的 http:// 或 https:// 接口地址，不含凭据、查询参数或片段。")
    return value


def validate_api_key(value: str) -> str:
    key = value.strip()
    if not key or any(character.isspace() or not character.isprintable() for character in key):
        raise StreamImageError("API Key 不能为空，也不能包含空白或控制字符，请重新填写。")
    return key


def mask_api_key(value: str) -> str:
    key = value.strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}{'*' * 8}{key[-4:]}"


def configuration_state() -> dict[str, Any]:
    values = read_env()
    raw_url = values.get("OPENAI_BASE_URL")
    raw_key = values.get("OPENAI_API_KEY")
    state: dict[str, Any] = {
        "ok": True,
        "env_file": str(ENV_PATH),
        "env_exists": ENV_PATH.is_file(),
        "base_url": None,
        "base_url_error": None,
        "api_key_configured": False,
        "api_key_masked": None,
        "api_key_error": None,
    }
    if isinstance(raw_url, str) and raw_url.strip():
        try:
            state["base_url"] = validate_base_url(raw_url)
        except StreamImageError as exc:
            state["base_url_error"] = str(exc)
    if isinstance(raw_key, str) and raw_key.strip():
        try:
            state["api_key_masked"] = mask_api_key(validate_api_key(raw_key))
            state["api_key_configured"] = True
        except StreamImageError as exc:
            state["api_key_error"] = str(exc)
    state["ready"] = bool(
        state["base_url"] and state["api_key_configured"] and not state["api_key_error"]
    )
    return state


def configuration_hint() -> str:
    return (
        "请让用户在浏览器中自行填写（Key 不经过对话）："
        f'运行 `uv run "{Path(__file__).resolve()}" configure --web`，'
        f"脚本会打开本地表单，保存到 {ENV_PATH}。"
    )


def load_configuration() -> tuple[str, str, Path]:
    state = configuration_state()
    problems = []
    if state["base_url"] is None:
        problems.append(state["base_url_error"] or "Base URL 未配置")
    if not state["api_key_configured"]:
        problems.append(state["api_key_error"] or "API Key 未配置")
    if problems:
        raise StreamImageError("配置不可用：" + "；".join(problems) + "。" + configuration_hint())
    return state["base_url"], read_env()["OPENAI_API_KEY"].strip(), ENV_PATH


def save_configuration(base_url: str | None = None, api_key: str | None = None) -> Path:
    settings = {}
    if base_url is not None:
        settings["OPENAI_BASE_URL"] = validate_base_url(base_url)
    if api_key is not None:
        settings["OPENAI_API_KEY"] = validate_api_key(api_key)
    if not settings:
        raise StreamImageError("请提供需要保存的 Base URL 或 API Key。")
    destination = ENV_PATH
    directory = destination.parent
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            if destination.exists():
                temporary.write(destination.read_text(encoding="utf-8"))
            temporary.flush()
            os.fsync(temporary.fileno())
        for name, value in settings.items():
            set_key(temporary_path, name, value)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
 body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: #f5f6f8;
        color: #1b1f24; margin: 0; padding: 40px 16px; }}
 main {{ max-width: 520px; margin: 0 auto; background: #fff; border-radius: 12px;
         padding: 28px; box-shadow: 0 2px 16px rgba(16,24,40,.08); }}
 h1 {{ font-size: 19px; margin: 0 0 8px; }}
 p {{ color: #5b6472; font-size: 13px; line-height: 1.7; margin: 0 0 16px; }}
 label {{ display: block; font-size: 13px; font-weight: 600; margin: 0 0 6px; }}
 .hint {{ font-size: 12px; color: #7a8494; font-weight: 400; margin-left: 6px; }}
 input {{ width: 100%; box-sizing: border-box; padding: 10px 12px; font-size: 14px;
          border: 1px solid #cfd6e0; border-radius: 8px; margin-bottom: 16px; font-family: inherit; }}
 input:focus {{ outline: 2px solid #2f6feb; outline-offset: 1px; border-color: #2f6feb; }}
 button {{ width: 100%; padding: 11px; font-size: 14px; font-weight: 600; color: #fff;
           background: #2f6feb; border: 0; border-radius: 8px; cursor: pointer; }}
 button:hover {{ background: #2559c4; }}
 .state {{ font-size: 12px; background: #f0f3f8; border-radius: 8px; padding: 10px 12px;
           margin-bottom: 20px; line-height: 1.7; color: #3c4654; }}
 .ok {{ font-size: 30px; text-align: center; margin-bottom: 8px; }}
 .fail {{ color: #b42318; }}
 code {{ background: #eceff4; padding: 1px 5px; border-radius: 4px; font-size: 11px; }}
 a {{ color: #2f6feb; font-size: 13px; }}
</style>
</head>
<body><main>{body}</main></body>
</html>
"""

CONFIGURE_FORM_BODY = """
  <h1>GD Image Gen 配置</h1>
  <p>在此页面填写的 Base URL 与 API Key 只会提交到本机运行的脚本进程，直接写入本地 .env 文件，不经过 AI 对话。</p>
  <div class="state">
    当前状态：Base URL {url_state}；API Key {key_state}<br>
    保存位置：<code>{env_path}</code>
  </div>
  <form method="post" action="/save">
    <input type="hidden" name="token" value="{token}">
    <label for="base_url">Base URL<span class="hint">留空则保留原值</span></label>
    <input id="base_url" name="base_url" type="url" placeholder="https://example.com/v1"
           autocomplete="off" spellcheck="false" value="{base_url_value}">
    <label for="api_key">API Key<span class="hint">留空则保留原值</span></label>
    <input id="api_key" name="api_key" type="password" autocomplete="off" spellcheck="false"
           placeholder="{key_placeholder}">
    <button type="submit">保存到本地</button>
  </form>
  <p>保存后可直接关闭此页面，脚本会自动退出。Base URL 需为 http(s) 地址，不含凭据、查询参数或片段。</p>
"""


def render_page(title: str, body: str) -> str:
    return PAGE_TEMPLATE.format(title=html.escape(title), body=body)


def _parse_form(body: str) -> dict[str, str]:
    parsed = parse_qs(body, keep_blank_values=True, strict_parsing=False)
    return {name: values[0] if values else "" for name, values in parsed.items()}


def _error_page(message: str) -> str:
    return render_page(
        "保存失败",
        f'<h1 class="fail">保存失败</h1><p>{message}</p><p><a href="/">返回并重新填写</a></p>',
    )


def serve_configuration_form(
    timeout_seconds: float = 300.0,
    open_browser: bool = True,
    on_ready: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    state = configuration_state()
    token = secrets.token_urlsafe(24)
    outcome: dict[str, Any] = {}
    finished = threading.Event()
    rendered_state = {
        "url_state": state["base_url"] or ("未配置" if not state["base_url_error"] else "格式有误"),
        "key_state": (
            f"已配置（{state['api_key_masked']}）"
            if state["api_key_configured"]
            else ("格式有误" if state["api_key_error"] else "未配置")
        ),
        "env_path": html.escape(str(ENV_PATH)),
        "token": html.escape(token, quote=True),
        "base_url_value": html.escape(state["base_url"] or "", quote=True),
        "key_placeholder": "留空保留已保存的 Key" if state["api_key_configured"] else "sk-...",
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "gd-image-gen"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - silence access logs
            return

        def _send(self, status: int, page: str) -> None:
            payload = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if urlsplit(self.path).path not in {"/", "/index.html"}:
                self._send(404, _error_page("页面不存在。"))
                return
            self._send(200, render_page("GD Image Gen 配置", CONFIGURE_FORM_BODY.format(**rendered_state)))

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            if urlsplit(self.path).path != "/save":
                self._send(404, _error_page("页面不存在。"))
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 64 * 1024:
                self._send(400, _error_page("表单内容无效。"))
                return
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            fields = _parse_form(raw)
            # compare_digest rejects non-ASCII str, and a hostile form can send any bytes.
            if not secrets.compare_digest(
                fields.get("token", "").encode("utf-8"), token.encode("utf-8")
            ):
                self._send(403, _error_page("表单令牌无效，请重新打开页面。"))
                return
            base_url = fields.get("base_url", "").strip() or None
            api_key = fields.get("api_key", "").strip() or None
            if base_url is None and api_key is None:
                self._send(400, _error_page("Base URL 和 API Key 均为空，没有需要保存的内容。"))
                return
            try:
                path = save_configuration(base_url=base_url, api_key=api_key)
            except StreamImageError as exc:
                self._send(400, _error_page(html.escape(str(exc))))
                return
            except OSError:
                self._send(500, _error_page("写入配置文件失败，请检查目录权限和磁盘空间。"))
                return
            outcome["saved"] = True
            outcome["env_file"] = str(path)
            saved = [name for name, value in (("Base URL", base_url), ("API Key", api_key)) if value]
            detail = "已保存：" + "、".join(saved) + "。"
            self._send(
                200,
                render_page(
                    "已保存",
                    f'<div class="ok">✓</div><h1>已保存</h1>'
                    f"<p>{detail}<br>已写入 <code>{html.escape(str(path))}</code>。"
                    f"可以关闭此页面，回到对话继续。</p>",
                ),
            )
            finished.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    host, port = server.server_address[0], server.server_address[1]
    url = f"http://{host}:{port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
    if not opened:
        print(f"配置页面（请在浏览器打开）: {url}", file=sys.stderr)
    if on_ready is not None:
        on_ready(url)
    try:
        finished.wait(timeout=timeout_seconds)
    finally:
        server.shutdown()
        server.server_close()
    if not outcome.get("saved"):
        raise StreamImageError(
            "配置表单未提交或已超时，未保存任何内容。请在浏览器打开下面的地址后重新填写："
            + url
        )
    return {"ok": True, "env_file": outcome["env_file"], "opened_browser": opened}


def read_prompt(prompt: str | None, prompt_file: str | None) -> str:
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


def decode_and_validate_image(encoded: str, requested_format: str) -> tuple[bytes, str, int, int]:
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise StreamImageError("API response contained invalid Base64 image data") from exc
    if not data:
        raise StreamImageError("API response contained empty image data")

    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            detected_format = str(image.format or "").upper()
            width, height = image.size
            image.load()
    except Exception as exc:
        raise StreamImageError("API response did not contain a valid complete image") from exc

    expected = "JPEG" if requested_format == "jpeg" else requested_format.upper()
    if detected_format != expected:
        raise StreamImageError(
            f"Image format mismatch: requested {expected}, received {detected_format or 'unknown'}"
        )
    if width <= 0 or height <= 0:
        raise StreamImageError("Image dimensions are invalid")
    return data, detected_format, width, height


def image_has_transparency(data: bytes) -> bool:
    """True when the image carries an alpha channel with at least one non-opaque pixel."""
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")
        if "A" not in image.getbands():
            return False
        low, _ = image.getchannel("A").getextrema()
        return low < 255


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
        raise StreamImageError(f"Unsupported image format: {image_format}")
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
        raise StreamImageError("Saved image SHA-256 does not match the API response")
    return destination, digest


def request_payload(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "n": 1,
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format,
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
    base_url: str,
    prompt: str,
) -> dict[str, Any]:
    endpoint = "/images/generations" if args.command == "generate" else "/images/edits"
    return {
        "ok": True,
        "dry_run": True,
        "operation": args.command,
        "env_file": str(ENV_PATH),
        "request_url": base_url + endpoint,
        "model": args.model,
        "prompt_length": len(prompt),
        "api_size": args.size,
        "output_dir": str(OUTPUT_DIR),
        "images": [str(path) for path in getattr(args, "image_paths", [])],
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    base_url, api_key, _ = load_configuration()
    prompt = read_prompt(args.prompt, args.prompt_file)

    if args.command == "edit":
        args.image_paths = [existing_file(raw, "Input image") for raw in args.image]
        args.mask_path = existing_file(args.mask, "Mask") if args.mask else None
    else:
        args.image_paths = []
        args.mask_path = None

    if args.dry_run:
        return dry_run_result(args, base_url, prompt)

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
            response = client.images.generate(**payload)
            if not response.data or not response.data[0].b64_json:
                raise StreamImageError("Non-streaming generation returned no image data")
        else:
            with ExitStack() as stack:
                image_files = [stack.enter_context(path.open("rb")) for path in args.image_paths]
                request = dict(payload)
                request["image"] = image_files if len(image_files) > 1 else image_files[0]
                if args.mask_path is not None:
                    request["mask"] = stack.enter_context(args.mask_path.open("rb"))
                response = client.images.edit(**request)
                if not response.data or not response.data[0].b64_json:
                    raise StreamImageError("Non-streaming edit returned no image data")
        completed_b64 = response.data[0].b64_json
    finally:
        client.close()

    data, image_format, width, height = decode_and_validate_image(
        completed_b64, args.output_format
    )
    has_transparency = image_has_transparency(data)
    output_dir = OUTPUT_DIR
    prefix = args.filename_prefix or ("generated" if args.command == "generate" else "edited")
    path, digest = save_image(data, image_format, output_dir, prefix)
    result = {
        "ok": True,
        "operation": args.command,
        "path": str(path),
        "display_path": display_path(path),
        "runtime_os": runtime_os(),
        "format": image_format,
        "width": width,
        "height": height,
        "api_size": args.size,
        "size": path.stat().st_size,
        "sha256": digest,
        "request_mode": "non_streaming",
        "background": args.background,
        "has_transparency": has_transparency,
    }
    if args.background == "transparent" and not has_transparency:
        result["warning"] = "请求了透明背景，但返回的图片没有透明像素；服务商或模型可能不支持该参数。"
    return result


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
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or edit an image through a user-configured OpenAI-compatible Images API"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser(
        "status", help="Report whether Base URL and API key are configured (key is masked)"
    )
    status.add_argument("--require", action="store_true", help="Exit non-zero when configuration is incomplete")
    subparsers.add_parser("save-key", help="Persist an API key from hidden terminal input or stdin")
    configure = subparsers.add_parser(
        "configure", help="Save Base URL and/or API key in the skill .env"
    )
    configure.add_argument("--web", action="store_true", help="Open a local browser form (recommended; key never enters the chat). Only run this for an actual image request that lacks configuration")
    configure.add_argument("--base-url", help="Save an HTTP(S) API base URL")
    configure.add_argument("--api-key", action="store_true", help="Read the key from hidden input or stdin (not an argument value)")
    configure.add_argument("--no-browser", action="store_true", help="With --web, print the URL instead of opening a browser")
    configure.add_argument("--timeout", type=float, default=300.0, help="With --web, seconds to wait for the form")

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
    if args.background == "transparent" and args.output_format == "jpeg":
        raise StreamImageError(
            "JPEG 不支持透明背景；--background transparent 需搭配 --output-format png 或 webp"
        )
    if args.output_compression is not None and not 0 <= args.output_compression <= 100:
        raise StreamImageError("--output-compression must be between 0 and 100")


def safe_error(exc: Exception) -> dict[str, Any]:
    status_code = getattr(exc, "status_code", None)
    error_type = type(exc).__name__
    messages = {
        401: "接口认证失败（401），当前 API Key 未被接受，请检查或更新 Key。",
        403: "接口拒绝访问（403），请检查账号、模型或接口权限；这不一定是 Key 无效。",
        404: "接口不存在（404），请检查 Base URL 的路径和图片接口支持情况。",
        429: "接口限制了请求（429），可能是限流或额度不足，请检查服务商的额度和限制。",
    }
    if isinstance(exc, StreamImageError):
        message = str(exc)
    elif error_type == "APITimeoutError":
        message = "请求超时，请检查网络和服务状态。"
    elif error_type == "APIConnectionError":
        message = "无法连接图片接口，请检查 Base URL、网络、DNS 和 TLS；连接失败不一定是地址错误。"
    elif status_code in messages:
        message = messages[status_code]
    elif isinstance(status_code, int) and status_code >= 500:
        message = "图片服务发生服务端错误，请稍后再试或联系服务商。"
    elif isinstance(exc, OSError):
        message = "本地文件读写失败，请检查文件路径、权限和磁盘空间。"
    else:
        message = "图片请求失败，请检查请求参数和接口支持情况。"
    return {
        "ok": False,
        "error_type": type(exc).__name__,
        "status_code": status_code if isinstance(status_code, int) else None,
        "error": message,
    }


def emit(payload: dict[str, Any], stream: Any = None) -> None:
    """Write one JSON object, keeping non-ASCII readable but never crashing on a narrow console."""
    target = stream if stream is not None else sys.stdout
    try:
        print(json.dumps(payload, ensure_ascii=False), file=target)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True), file=target)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = configuration_state()
            emit(result)
            return 0 if result["ready"] or not args.require else 2
        if args.command in {"save-key", "configure"}:
            if args.command == "configure" and args.web:
                result = serve_configuration_form(
                    timeout_seconds=args.timeout, open_browser=not args.no_browser
                )
            else:
                from getpass import getpass

                base_url = getattr(args, "base_url", None)
                read_key = args.command == "save-key" or args.api_key
                if args.command == "configure" and base_url is None and not read_key:
                    if not sys.stdin.isatty():
                        raise StreamImageError(
                            "请使用 configure --web 在浏览器中填写（推荐），"
                            "或用 --base-url 和/或 --api-key 指定需要配置的项目。"
                        )
                    base_url = input("Base URL（留空保留原配置）: ").strip() or None
                    key = getpass("API Key（留空保留原配置）: ").strip() or None
                else:
                    key = (getpass("API Key: ") if sys.stdin.isatty() else sys.stdin.read()) if read_key else None
                path = save_configuration(base_url=base_url, api_key=key)
                result = {"ok": True, "env_file": str(path)}
        else:
            validate_args(args)
            result = execute(args)
    except Exception as exc:
        emit(safe_error(exc), sys.stderr)
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
