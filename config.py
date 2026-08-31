import hashlib
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# Local: .env then .env.local (Vercel CLI). Production: Vercel injects process env.
def _load_env_files() -> None:
    """Load env files; local .env wins over .env.local (Vercel pull placeholders)."""
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
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and set all values."
        )
    return value


class Config:
    def __init__(self):
        self.SECRET_KEY = _require("SECRET_KEY")
        self.APP_USERNAME = _require("APP_USERNAME")
        self.APP_PASSWORD = _require("APP_PASSWORD")
        self.WIALON_TOKEN = _require("WIALON_TOKEN")
        self.WIALON_RESOURCE_ID = int(_require("WIALON_RESOURCE_ID"))
        self.MAX_CONTENT_LENGTH = 32 * 1024 * 1024
        self.CONTROLTECH_URL = os.getenv(
            "CONTROLTECH_URL", "https://www.controltech-ea.com/"
        ).strip()


config = Config()


def get_auth_credentials():
    """Read current login credentials from env (reloaded on each login check)."""
    _load_env_files()
    username = os.getenv("APP_USERNAME", "").strip()
    password = os.getenv("APP_PASSWORD", "").strip()
    if username == "[SENSITIVE]":
        username = ""
    if password == "[SENSITIVE]":
        password = ""
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
