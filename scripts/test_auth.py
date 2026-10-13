import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
from unittest.mock import patch

import stream_image
from stream_image import load_api_key, load_base_url, save_api_key, StreamImageError


with tempfile.TemporaryDirectory() as temporary:
    home = Path(temporary)
    stream_image.ENV_PATH = home / ".env"
    script_directory = home / "scripts"
    script_directory.mkdir()
    shutil.copyfile(Path(__file__).with_name("stream_image.py"), script_directory / "stream_image.py")
    stream_image.ENV_PATH.write_text("# Keep this\nOTHER=value\n", encoding="utf-8")
    (home / "auth.js").write_text("invalid", encoding="utf-8")
    (home / "auth.json").write_text('{"OPENAI_API_KEY":"codex-test"}', encoding="utf-8")
    assert load_api_key(home)[0] == "codex-test"
    (home / "config.toml").write_text(
        'experimental_bearer_token = "config-test"\n'
        'model_provider = "ai"\n'
        '[model_providers.ai]\n'
        'base_url = "https://config.example/v1/"\n',
        encoding="utf-8",
    )
    assert load_api_key(home)[0] == "config-test"
    assert load_base_url(home)[0] == "https://config.example/v1"
    assert (
        stream_image.build_parser().parse_args(["generate", "--prompt", "test"]).model
        == "gpt-image-2.5-flare"
    )
    assert load_api_key(home, str(home / "auth.json"))[0] == "codex-test"
    for top_level in ('', 'experimental_bearer_token = "   "\n', 'experimental_bearer_token = 123\n'):
        (home / "config.toml").write_text(
            top_level + 'model_provider = "ai"\n'
            '[model_providers.other]\nexperimental_bearer_token = "other-test"\n'
            '[model_providers.ai]\nexperimental_bearer_token = " provider-test "\n',
            encoding="utf-8",
        )
        assert load_api_key(home) == ("provider-test", home / "config.toml")
    for config_text in ('experimental_bearer_token = " "\n', 'invalid toml',
                        'model_provider = "ai"\n[model_providers.ai]\nexperimental_bearer_token = 123\n'):
        (home / "config.toml").write_text(config_text, encoding="utf-8")
        assert load_api_key(home)[0] == "codex-test"
    (home / "config.toml").unlink()
    original = (home / "auth.json").read_bytes()
    command = [sys.executable, str(script_directory / "stream_image.py"), "save-key"]
    result = subprocess.run(command, input="fallback-test\n", text=True, capture_output=True,
                            env={**os.environ, "CODEX_HOME": str(home)})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert "fallback-test" not in result.stdout + result.stderr
    assert load_api_key(home)[0] == "fallback-test"
    assert stream_image.ENV_PATH.read_text(encoding="utf-8").startswith("# Keep this\nOTHER=value\n")
    assert not (home / "gd-image-gen" / "auth.json").exists()
    assert (home / "auth.json").read_bytes() == original
    assert load_api_key(home, str(home / "auth.json"))[0] == "codex-test"
    (home / "auth.js").write_text('{"OPENAI_API_KEY":"codex-js-test"}', encoding="utf-8")
    assert load_api_key(home)[0] == "fallback-test"
    (home / "auth.json").unlink()
    assert load_api_key(home)[0] == "fallback-test"
    (home / "auth.js").write_text("invalid", encoding="utf-8")
    (home / "auth.json").write_text("{}", encoding="utf-8")
    assert load_api_key(home)[0] == "fallback-test"
    (home / "auth.json").unlink()
    assert load_api_key(home)[0] == "fallback-test"
    try:
        load_api_key(home, str(home / "auth.js"))
    except StreamImageError:
        pass
    else:
        raise AssertionError("Explicit invalid credentials must not fall back")
    for invalid in ("", "a b", "a\nb", "a\x00b"):
        try:
            save_api_key(invalid)
        except StreamImageError:
            pass
        else:
            raise AssertionError("Invalid key accepted")
    with patch("stream_image.os.replace", side_effect=OSError("simulated failure")):
        try:
            save_api_key("replacement-test")
        except OSError:
            pass
        else:
            raise AssertionError("Write failure ignored")
    assert load_api_key(home)[0] == "fallback-test"
    assert not list(home.glob("tmp*"))
    special_key = "test-'quoted'-${LITERAL}-#-\\end"
    save_api_key(special_key)
    assert load_api_key(home)[0] == special_key
    stream_image.save_configuration(base_url="https://local.example/v1/", api_key="configured-test")
    assert load_base_url(home) == ("https://local.example/v1", stream_image.ENV_PATH)
    assert load_api_key(home)[0] == "configured-test"
    stream_image.save_configuration(base_url="https://updated.example/v1")
    assert load_api_key(home)[0] == "configured-test"
    before = stream_image.ENV_PATH.read_bytes()
    for url in ("", "example.com", "ftp://example.com", "https://user:secret@example.com", "https://example.com?key=secret", "https://example.com:bad"):
        try:
            stream_image.save_configuration(base_url=url, api_key="changed")
        except StreamImageError:
            pass
        else:
            raise AssertionError("Invalid URL accepted")
        assert stream_image.ENV_PATH.read_bytes() == before
    stream_image.ENV_PATH.unlink()
    args = stream_image.build_parser().parse_args(["generate", "--prompt", "test", "--dry-run"])
    stream_image.validate_args(args)
    try:
        with patch("stream_image.codex_home", return_value=home):
            stream_image.execute(args)
    except StreamImageError as exc:
        assert "API Key:" in str(exc) and "Base URL:" in str(exc)
    else:
        raise AssertionError("Missing configuration accepted")
    from openai import APIConnectionError, APITimeoutError, AuthenticationError, PermissionDeniedError, RateLimitError, InternalServerError
    import httpx
    request = httpx.Request("POST", "https://example.com/v1/images/generations")
    for error, expected in ((APIConnectionError(request=request), "无法连接"), (APITimeoutError(request=request), "超时")):
        assert expected in stream_image.safe_error(error)["error"]
    for cls, status in ((AuthenticationError, 401), (PermissionDeniedError, 403), (RateLimitError, 429), (InternalServerError, 500)):
        error = cls("secret-key-must-not-leak", response=httpx.Response(status, request=request), body=None)
        result = stream_image.safe_error(error)
        assert result["status_code"] == status
        assert "secret-key" not in json.dumps(result)
    result = subprocess.run([sys.executable, str(script_directory / "stream_image.py"), "configure", "--base-url", "https://cli.example/v1", "--api-key"], input="cli-secret-test", text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "cli-secret-test" not in result.stdout + result.stderr
    assert load_base_url(home)[0] == "https://cli.example/v1"
    assert load_api_key(home)[0] == "cli-secret-test"
print("Configuration and error feedback checks passed")
