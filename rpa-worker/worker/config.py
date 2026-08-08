"""Environment-only worker configuration."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Raised when worker configuration is invalid."""


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def _positive_float(name: str, default: str) -> float:
    raw = os.environ.get(name, default).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class Settings:
    api_base_url: str
    worker_token: str
    poll_interval: float
    http_timeout: float
    work_dir: Path
    commands: dict[str, str]

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = _required("API_BASE_URL").rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError("API_BASE_URL must be an absolute HTTP(S) URL")
        allow_insecure_http = os.environ.get(
            "ALLOW_INSECURE_HTTP", ""
        ).strip().lower() in {"1", "true", "yes"}
        if (
            parsed.scheme == "http"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            and not allow_insecure_http
        ):
            raise ConfigError(
                "API_BASE_URL must use HTTPS outside loopback; "
                "set ALLOW_INSECURE_HTTP=true only for trusted development networks"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigError(
                "API_BASE_URL must not contain credentials, query, or fragment"
            )
        work_dir = Path(os.environ.get("WORK_DIR", "./work").strip() or "./work")
        commands = {
            "千牛": os.environ.get("QIANNIU_COMMAND", "").strip(),
            "万相台": os.environ.get("WANXIANGTAI_COMMAND", "").strip(),
            "灰豚": os.environ.get("HUITUN_COMMAND", "").strip(),
        }
        return cls(
            api_base_url=base_url,
            worker_token=_required("WORKER_TOKEN"),
            poll_interval=_positive_float("POLL_INTERVAL", "10"),
            http_timeout=_positive_float("HTTP_TIMEOUT", "30"),
            work_dir=work_dir.expanduser().resolve(),
            commands=commands,
        )
