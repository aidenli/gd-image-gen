"""Self-checks for configuration handling, error feedback, and the local config form.

Run with: uv run --with openai --with pillow --with python-dotenv --with httpx scripts/test_auth.py
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

import stream_image
from stream_image import (
    configuration_state,
    load_configuration,
    save_configuration,
    StreamImageError,
)


def no_codex_fallback(home: Path) -> None:
    """Codex credentials must never be picked up implicitly."""
    (home / "auth.json").write_text('{"OPENAI_API_KEY":"codex-test"}', encoding="utf-8")
    (home / "auth.js").write_text('{"OPENAI_API_KEY":"codex-js-test"}', encoding="utf-8")
    (home / "config.toml").write_text(
        'experimental_bearer_token = "config-test"\n'
        'model_provider = "ai"\n'
        '[model_providers.ai]\n'
        'base_url = "https://config.example/v1/"\n'
        'experimental_bearer_token = "provider-test"\n',
        encoding="utf-8",
    )
    state = configuration_state()
    assert state["ready"] is False
    assert state["base_url"] is None and state["api_key_configured"] is False
    try:
        load_configuration()
    except StreamImageError as exc:
        message = str(exc)
        assert "Base URL 未配置" in message and "API Key 未配置" in message
        assert "configure --web" in message
    else:
        raise AssertionError("Codex credentials must not be used as a fallback")


def hint_command_is_runnable() -> None:
    """Regression: the hint used to emit a bare filename that `uv run` cannot spawn."""
    hint = stream_image.configuration_hint()
    script = Path(__file__).with_name("stream_image.py").resolve()
    assert str(script) in hint, hint
    assert "configure --web" in hint, hint
    assert script.is_file()
    # The quoted absolute path must actually be spawnable from an unrelated cwd.
    result = subprocess.run(
        [sys.executable, str(script), "status"],
        text=True, capture_output=True, cwd=str(script.parent.parent),
    )
    assert result.returncode == 0, result.stderr


def masking_and_partial_updates() -> None:
    stream_image.ENV_PATH.write_text("# Keep this\nOTHER=value\n", encoding="utf-8")
    save_configuration(base_url="https://local.example/v1/", api_key="configured-secret-key")
    assert load_configuration()[:2] == ("https://local.example/v1", "configured-secret-key")
    state = configuration_state()
    assert state["ready"] is True
    assert state["api_key_masked"] == "con********-key"
    assert "configured-secret-key" not in json.dumps(state)
    assert stream_image.ENV_PATH.read_text(encoding="utf-8").startswith("# Keep this\nOTHER=value\n")
    assert stream_image.ENV_PATH.read_text(encoding="utf-8").count("OPENAI_API_KEY") == 1

    save_configuration(base_url="https://updated.example/v1")
    assert load_configuration()[:2] == ("https://updated.example/v1", "configured-secret-key")
    save_configuration(api_key="rotated-secret-key")
    assert load_configuration()[:2] == ("https://updated.example/v1", "rotated-secret-key")


def invalid_values_are_rejected() -> None:
    save_configuration(base_url="https://stable.example/v1", api_key="stable-secret-key")
    before = stream_image.ENV_PATH.read_bytes()
    for url in ("", "example.com", "ftp://example.com", "https://user:secret@example.com",
                "https://example.com?key=secret", "https://example.com#frag",
                "https://example.com:bad"):
        try:
            save_configuration(base_url=url, api_key="changed")
        except StreamImageError:
            pass
        else:
            raise AssertionError(f"Invalid URL accepted: {url!r}")
        assert stream_image.ENV_PATH.read_bytes() == before
    for invalid in ("", "a b", "a\nb", "a\x00b", "   "):
        try:
            save_configuration(api_key=invalid)
        except StreamImageError:
            pass
        else:
            raise AssertionError(f"Invalid key accepted: {invalid!r}")
        assert stream_image.ENV_PATH.read_bytes() == before


def corrupted_values_are_reported() -> None:
    stream_image.ENV_PATH.write_text(
        "OPENAI_BASE_URL=example.com\nOPENAI_API_KEY=\n", encoding="utf-8"
    )
    state = configuration_state()
    assert state["ready"] is False
    assert state["base_url_error"] and state["base_url"] is None
    try:
        load_configuration()
    except StreamImageError as exc:
        assert "Base URL" in str(exc) and "API Key" in str(exc)
    else:
        raise AssertionError("Corrupted configuration accepted")


def atomic_write_survives_failure() -> None:
    save_configuration(base_url="https://stable.example/v1", api_key="stable-secret-key")
    with patch("stream_image.os.replace", side_effect=OSError("simulated failure")):
        try:
            save_configuration(api_key="replacement-test")
        except OSError:
            pass
        else:
            raise AssertionError("Write failure ignored")
    assert load_configuration()[1] == "stable-secret-key"
    assert not list(stream_image.ENV_PATH.parent.glob("tmp*"))


def special_characters_round_trip() -> None:
    special_key = "test-'quoted'-${LITERAL}-#-\\end"
    save_configuration(api_key=special_key)
    assert load_configuration()[1] == special_key


def dry_run_and_missing_config(home: Path) -> None:
    args = stream_image.build_parser().parse_args(["generate", "--prompt", "test", "--dry-run"])
    stream_image.validate_args(args)
    result = stream_image.execute(args)
    assert result["dry_run"] is True and result["request_url"].endswith("/images/generations")
    assert "display_path" not in result
    # Images belong to the skill itself, not to CODEX_HOME.
    assert result["output_dir"] == str(stream_image.SKILL_ROOT / "generated_images")
    assert "CODEX_HOME" not in result["output_dir"]
    stream_image.ENV_PATH.unlink()
    try:
        stream_image.execute(args)
    except StreamImageError as exc:
        assert "configure --web" in str(exc)
    else:
        raise AssertionError("Missing configuration accepted")


def safe_errors_hide_secrets() -> None:
    from openai import (
        APIConnectionError, APITimeoutError, AuthenticationError,
        PermissionDeniedError, RateLimitError, InternalServerError,
    )
    import httpx

    request = httpx.Request("POST", "https://example.com/v1/images/generations")
    for error, expected in (
        (APIConnectionError(request=request), "无法连接"),
        (APITimeoutError(request=request), "超时"),
    ):
        assert expected in stream_image.safe_error(error)["error"]
    for cls, status in (
        (AuthenticationError, 401), (PermissionDeniedError, 403),
        (RateLimitError, 429), (InternalServerError, 500),
    ):
        error = cls("secret-key-must-not-leak", response=httpx.Response(status, request=request), body=None)
        result = stream_image.safe_error(error)
        assert result["status_code"] == status
        assert "secret-key" not in json.dumps(result)


def _png_b64(mode: str, color) -> str:
    import base64
    from io import BytesIO
    from PIL import Image

    buffer = BytesIO()
    Image.new(mode, (4, 4), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def transparent_jpeg_is_rejected() -> None:
    parser = stream_image.build_parser()
    args = parser.parse_args(
        ["generate", "--prompt", "x", "--background", "transparent", "--output-format", "jpeg"]
    )
    try:
        stream_image.validate_args(args)
    except StreamImageError as exc:
        assert "png" in str(exc) and "webp" in str(exc)
    else:
        raise AssertionError("Transparent JPEG must be rejected before any request")
    for output_format in ("png", "webp"):
        stream_image.validate_args(parser.parse_args(
            ["generate", "--prompt", "x", "--background", "transparent", "--output-format", output_format]
        ))


def transparency_detection() -> None:
    import base64

    decode = lambda encoded: base64.b64decode(encoded)
    assert stream_image.image_has_transparency(decode(_png_b64("RGBA", (255, 0, 0, 0)))) is True
    assert stream_image.image_has_transparency(decode(_png_b64("RGBA", (255, 0, 0, 255)))) is False
    assert stream_image.image_has_transparency(decode(_png_b64("RGB", (255, 0, 0)))) is False


def transparent_result_reports_alpha(output_dir: Path) -> None:
    from types import SimpleNamespace

    save_configuration(base_url="https://example.com/v1", api_key="sk-test-transparent")
    parser = stream_image.build_parser()
    for encoded, expect_alpha in ((_png_b64("RGBA", (0, 0, 0, 0)), True), (_png_b64("RGB", (255, 255, 255)), False)):
        client = SimpleNamespace(
            images=SimpleNamespace(generate=lambda **_: SimpleNamespace(data=[SimpleNamespace(b64_json=encoded)])),
            close=lambda: None,
        )
        args = parser.parse_args(["generate", "--prompt", "sticker", "--background", "transparent"])
        stream_image.validate_args(args)
        with patch("openai.OpenAI", return_value=client), patch.object(stream_image, "OUTPUT_DIR", output_dir):
            result = stream_image.execute(args)
        assert result["background"] == "transparent"
        assert result["has_transparency"] is expect_alpha
        assert ("warning" in result) is (not expect_alpha)
        assert Path(result["path"]).parent == output_dir.resolve()
    # Later checks expect an unconfigured skill.
    stream_image.ENV_PATH.unlink()


def cli_status_and_stdin_key(script: str, env: dict) -> None:
    result = subprocess.run([sys.executable, script, "status"], text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ready"] is False
    result = subprocess.run(
        [sys.executable, script, "status", "--require"], text=True, capture_output=True, env=env
    )
    assert result.returncode == 2

    result = subprocess.run(
        [sys.executable, script, "configure", "--base-url", "https://cli.example/v1", "--api-key"],
        input="cli-secret-test", text=True, capture_output=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "cli-secret-test" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["env_file"] == str(stream_image.ENV_PATH)
    assert load_configuration()[:2] == ("https://cli.example/v1", "cli-secret-test")

    result = subprocess.run([sys.executable, script, "status"], text=True, capture_output=True, env=env)
    status = json.loads(result.stdout)
    assert status["ready"] is True
    assert "cli-secret-test" not in result.stdout
    assert status["api_key_masked"] == "cli********test"


def _submit(page_url: str, fields: dict) -> tuple[int, str]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(page_url + "save", data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def configuration_form_flow() -> None:
    """The key must reach the local process over HTTP, never the command line."""
    stream_image.ENV_PATH.unlink(missing_ok=True)
    captured: dict = {}
    ready = threading.Event()

    def run_form() -> None:
        captured["result"] = stream_image.serve_configuration_form(
            timeout_seconds=30, open_browser=False,
            on_ready=lambda url: captured.__setitem__("url", url) or ready.set(),
        )

    form_thread = threading.Thread(target=run_form, daemon=True)
    form_thread.start()
    assert ready.wait(timeout=15), "form server did not start"
    page_url = captured["url"]
    assert page_url.startswith("http://127.0.0.1:")

    with urllib.request.urlopen(page_url, timeout=10) as response:
        page = response.read().decode("utf-8")
    assert response.status == 200
    assert "GD Image Gen 配置" in page
    token = page.split('name="token" value="')[1].split('"')[0]

    status, _ = _submit(page_url, {"token": "wrong", "base_url": "https://evil.example/v1", "api_key": "x"})
    assert status == 403
    assert not stream_image.ENV_PATH.exists()

    # Regression: a non-ASCII token used to raise TypeError inside compare_digest,
    # dropping the connection instead of returning 403.
    status, body = _submit(page_url, {"token": "中文令牌", "base_url": "https://evil.example/v1", "api_key": "x"})
    assert status == 403, (status, body)
    assert "令牌无效" in body
    assert not stream_image.ENV_PATH.exists()

    status, body = _submit(page_url, {"token": token, "base_url": "example.com", "api_key": "form-secret"})
    assert status == 400 and "格式不正确" in body
    assert not stream_image.ENV_PATH.exists()

    status, body = _submit(
        page_url, {"token": token, "base_url": "https://form.example/v1/", "api_key": "form-secret-key"}
    )
    assert status == 200 and "已保存" in body
    assert "form-secret-key" not in body
    form_thread.join(timeout=10)
    assert captured["result"]["ok"] is True
    assert captured["result"]["env_file"] == str(stream_image.ENV_PATH)
    assert load_configuration()[:2] == ("https://form.example/v1", "form-secret-key")


def abandoned_form_fails_loudly() -> None:
    stream_image.ENV_PATH.unlink(missing_ok=True)
    try:
        stream_image.serve_configuration_form(timeout_seconds=0.5, open_browser=False)
    except StreamImageError as exc:
        assert "未保存" in str(exc) or "超时" in str(exc)
    else:
        raise AssertionError("Abandoned form must not report success")
    assert not stream_image.ENV_PATH.exists()


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        # Resolve symlinks (macOS /var -> /private/var) to match the script's own resolved paths.
        home = Path(temporary).resolve()
        stream_image.ENV_PATH = home / ".env"
        script_directory = home / "scripts"
        script_directory.mkdir()
        shutil.copyfile(Path(__file__).with_name("stream_image.py"), script_directory / "stream_image.py")
        script = str(script_directory / "stream_image.py")
        env = {**os.environ, "CODEX_HOME": str(home)}

        checks = [
            ("no_codex_fallback", lambda: no_codex_fallback(home)),
            ("hint_command_is_runnable", hint_command_is_runnable),
            ("masking_and_partial_updates", masking_and_partial_updates),
            ("invalid_values_are_rejected", invalid_values_are_rejected),
            ("corrupted_values_are_reported", corrupted_values_are_reported),
            ("atomic_write_survives_failure", atomic_write_survives_failure),
            ("special_characters_round_trip", special_characters_round_trip),
            ("dry_run_and_missing_config", lambda: dry_run_and_missing_config(home)),
            ("safe_errors_hide_secrets", safe_errors_hide_secrets),
            ("transparent_jpeg_is_rejected", transparent_jpeg_is_rejected),
            ("transparency_detection", transparency_detection),
            ("transparent_result_reports_alpha", lambda: transparent_result_reports_alpha(home / "images")),
            ("cli_status_and_stdin_key", lambda: cli_status_and_stdin_key(script, env)),
            ("configuration_form_flow", configuration_form_flow),
            ("abandoned_form_fails_loudly", abandoned_form_fails_loudly),
        ]
        for name, check in checks:
            check()
            print(f"  ok  {name}")
    print("Configuration and error feedback checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
