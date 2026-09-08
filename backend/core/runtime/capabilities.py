"""Runtime capability contract shared by aggregate and health endpoints."""

from .config import RuntimeConfig


MOBILE_API_VERSION = 1


def runtime_capabilities(config: RuntimeConfig) -> dict[str, bool]:
    return {
        "native_notifications": config.mobile_mode,
        "remote_auth_recovery": not config.mobile_mode,
        "system_mail": not config.mobile_mode,
        "mobile_local_backend": config.mobile_mode,
    }
