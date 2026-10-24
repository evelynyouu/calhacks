"""Fails if an API key from .env (or the environment) is in any file git tracks, or if a .env
file is tracked. Runs with the rest of `pytest -q`, which everyone runs before pushing."""
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN")


def _secret_values() -> dict:
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"^([A-Za-z_]\w*)=(.*)$", line.strip())
            if m:
                values[m.group(1)] = m.group(2).split("#")[0].strip().strip("\"'")
    values.update({k: v for k, v in os.environ.items() if SECRET_NAME.search(k)})
    return {k: v for k, v in values.items() if SECRET_NAME.search(k) and len(v) >= 12}


def _tracked_files() -> list:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.decode().split("\0") if f]


def test_no_env_file_is_tracked():
    tracked = [f for f in _tracked_files() if re.search(r"(^|/)\.env($|\.)", f) and not f.endswith(".env.example")]
    assert tracked == [], f"env files are tracked by git (they hold keys): {tracked}"


def test_no_api_key_in_tracked_files():
    secrets = _secret_values()
    if not secrets:
        pytest.skip("no keys in .env or the environment to look for")
    leaks = []
    for f in _tracked_files():
        try:
            text = (ROOT / f).read_text(errors="ignore")
        except (OSError, IsADirectoryError):
            continue
        leaks += [f"{f}: contains {name}" for name, value in secrets.items() if value in text]
    assert leaks == [], "API keys found in tracked files:\n" + "\n".join(leaks)
