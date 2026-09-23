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
    (home / "config.toml").unlink()
    original = (home / "auth.json").read_bytes()
    command = [sys.executable, str(script_directory / "stream_image.py"), "save-key"]
    result = subprocess.run(command, input="fallback-test\n", text=True, capture_output=True,
                            env={**os.environ, "CODEX_HOME": str(home)})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert "fallback-test" not in result.stdout + result.stderr
    assert load_api_key(home)[0] == "codex-test"
    assert stream_image.ENV_PATH.read_text(encoding="utf-8").startswith("# Keep this\nOTHER=value\n")
    assert not (home / "gd-image-gen" / "auth.json").exists()
    assert (home / "auth.json").read_bytes() == original
    assert load_api_key(home, str(home / "auth.json"))[0] == "codex-test"
    (home / "auth.json").unlink()
    (home / "auth.js").write_text('{"OPENAI_API_KEY":"codex-js-test"}', encoding="utf-8")
    assert load_api_key(home)[0] == "codex-test"
    (home / "auth.json").unlink()
    assert load_api_key(home)[0] == "codex-js-test"
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
print("Auth persistence checks passed")
