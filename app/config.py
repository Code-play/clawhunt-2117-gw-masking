"""Gateway configuration.

Every value that can hold a secret or an endpoint is read from the environment
only (order convention: "configuration and secrets are read exclusively from
environment variables").  Nothing in this module logs a value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MASK_TTL_SECONDS = 600
DEFAULT_MASK_FLAG = "mask_enabled"


class ConfigError(RuntimeError):
    """Raised when an environment variable is missing or malformed."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    """Process-wide settings.

    ``mask_key`` is the raw ``GW_MASK_KEY`` value; it is only turned into key
    material inside :mod:`app.masking.crypto` so that no log or repr ever shows
    it.  Use :meth:`redacted` when printing settings.
    """

    mask_key: str | None = None
    mask_ttl_seconds: int = DEFAULT_MASK_TTL_SECONDS
    mask_enabled_default: bool = True
    redis_url: str = "redis://127.0.0.1:6379/0"
    database_url: str = "postgresql+psycopg://gw:gw@127.0.0.1:5432/gw"
    vendor_base_url: str = "http://127.0.0.1:9/v1"
    vendor_timeout_seconds: float = 10.0
    environment: str = "local"

    def redacted(self) -> dict[str, object]:
        """Safe representation for logs and diagnostics."""

        return {
            "mask_key": "<set>" if self.mask_key else "<unset>",
            "mask_ttl_seconds": self.mask_ttl_seconds,
            "mask_enabled_default": self.mask_enabled_default,
            "redis_url": _redact_url(self.redis_url),
            "database_url": _redact_url(self.database_url),
            "vendor_base_url": self.vendor_base_url,
            "vendor_timeout_seconds": self.vendor_timeout_seconds,
            "environment": self.environment,
        }


def _redact_url(url: str) -> str:
    """Drop userinfo from a URL so credentials never reach a log line."""

    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        hostpart = rest.rsplit("@", 1)[1]
        return f"{scheme}://***@{hostpart}"
    return url


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean")


def load_settings() -> Settings:
    """Read settings from the process environment."""

    return Settings(
        mask_key=os.environ.get("GW_MASK_KEY"),
        mask_ttl_seconds=_env_int("GW_MASK_TTL_SECONDS", DEFAULT_MASK_TTL_SECONDS),
        mask_enabled_default=_env_bool("GW_MASK_ENABLED_DEFAULT", True),
        redis_url=os.environ.get("GW_REDIS_URL", "redis://127.0.0.1:6379/0"),
        database_url=os.environ.get(
            "GW_DATABASE_URL", "postgresql+psycopg://gw:gw@127.0.0.1:5432/gw"
        ),
        vendor_base_url=os.environ.get("GW_VENDOR_BASE_URL", "http://127.0.0.1:9/v1"),
        vendor_timeout_seconds=float(os.environ.get("GW_VENDOR_TIMEOUT_SECONDS", "10")),
        environment=os.environ.get("GW_ENV", "local"),
    )
