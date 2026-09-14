import hashlib
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
_ON_VERCEL = os.getenv("VERCEL") == "1"


def _load_env_files() -> None:
    """Load local env files only; never override Vercel-injected process env."""
    if _ON_VERCEL:
        return
    local = BASE_DIR / ".env.local"
    env = BASE_DIR / ".env"
    if local.is_file():
        load_dotenv(local, override=False)
    if env.is_file():
        load_dotenv(env, override=True)


_load_env_files()

__all__ = ["BASE_DIR", "config", "Config"]

_REQUIRED = (
    "SECRET_KEY",
    "APP_USERNAME",
    "APP_PASSWORD",
    "WIALON_TOKEN",
    "WIALON_RESOURCE_ID",
)


def _require(name: str) -> str:
    value = os.getenv(name, "").strip().strip('"').strip("'")
    if not value or value == "[SENSITIVE]":
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it in Vercel Project Settings or copy .env.example to .env."
        )
    return value


def _parse_resource_id(raw: str) -> int:
    try:
        return int(raw.strip().strip('"').strip("'"))
    except ValueError:
        raise RuntimeError(
            f"WIALON_RESOURCE_ID must be an integer, got: {raw!r}"
        )


class Config:
    def __init__(self):
        self.SECRET_KEY = _require("SECRET_KEY")
        self.APP_USERNAME = _require("APP_USERNAME")
        self.APP_PASSWORD = _require("APP_PASSWORD")
        self.WIALON_TOKEN = _require("WIALON_TOKEN")
        self.WIALON_RESOURCE_ID = _parse_resource_id(_require("WIALON_RESOURCE_ID"))
        self.MAX_CONTENT_LENGTH = 32 * 1024 * 1024
        self.CONTROLTECH_URL = os.getenv(
            "CONTROLTECH_URL", "https://www.controltech-ea.com/"
        ).strip()


config = Config()


def get_auth_credentials():
    """Return login credentials from env (Vercel) or reloaded local .env."""
    if _ON_VERCEL:
        return config.APP_USERNAME, config.APP_PASSWORD
    _load_env_files()
    username = os.getenv("APP_USERNAME", "").strip().strip('"').strip("'")
    password = os.getenv("APP_PASSWORD", "").strip().strip('"').strip("'")
    if username in ("", "[SENSITIVE]"):
        username = config.APP_USERNAME
    if password in ("", "[SENSITIVE]"):
        password = config.APP_PASSWORD
    return username, password


def auth_session_key() -> str:
    """Fingerprint of current credentials; changes when username/password change."""
    username, password = get_auth_credentials()
    payload = f"{username}\0{password}".encode()
    return hashlib.sha256(payload).hexdigest()


def verify_login(username: str, password: str) -> bool:
    expected_user, expected_pass = get_auth_credentials()
    username = username.strip()
    password = password.strip()
    if not expected_user or not expected_pass:
        return False
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(
        password, expected_pass
    )
