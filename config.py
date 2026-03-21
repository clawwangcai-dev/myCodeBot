from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    claude_bin: str
    claude_workdir: Path
    claude_settings_file: Path | None
    claude_output_format: str
    claude_streaming: bool
    claude_permission_mode: str | None
    claude_allowed_tools: list[str]
    claude_disallowed_tools: list[str]
    claude_timeout_seconds: int
    telegram_poll_timeout: int
    telegram_edit_interval_seconds: float
    telegram_api_base: str
    session_store_path: Path
    status_web_enabled: bool
    status_web_host: str
    status_web_port: int
    status_web_token: str | None


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Invalid boolean value: {value}")


def load_settings() -> Settings:
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('Missing TELEGRAM_BOT_TOKEN')

    workdir = Path(os.environ.get('CLAUDE_WORKDIR', os.getcwd())).expanduser().resolve()
    settings_file_raw = os.environ.get('CLAUDE_SETTINGS_FILE', '').strip()
    settings_file = Path(settings_file_raw).expanduser().resolve() if settings_file_raw else None
    output_format = os.environ.get('CLAUDE_OUTPUT_FORMAT', 'json').strip() or 'json'

    if output_format != 'json':
        raise RuntimeError('CLAUDE_OUTPUT_FORMAT must be json for this bridge')

    base_dir = Path(__file__).resolve().parent
    store_path = Path(os.environ.get('SESSION_STORE_PATH', 'sessions.json')).expanduser()
    if not store_path.is_absolute():
        store_path = base_dir / store_path

    poll_timeout_raw = os.environ.get('TELEGRAM_POLL_TIMEOUT', '30').strip() or '30'
    claude_timeout_raw = os.environ.get('CLAUDE_TIMEOUT_SECONDS', '300').strip() or '300'
    edit_interval_raw = os.environ.get('TELEGRAM_EDIT_INTERVAL_SECONDS', '1.0').strip() or '1.0'
    status_web_port_raw = os.environ.get('STATUS_WEB_PORT', '8765').strip() or '8765'

    return Settings(
        telegram_bot_token=token,
        claude_bin=os.environ.get('CLAUDE_BIN', 'claude').strip() or 'claude',
        claude_workdir=workdir,
        claude_settings_file=settings_file,
        claude_output_format=output_format,
        claude_streaming=_parse_bool(os.environ.get('CLAUDE_STREAMING'), default=False),
        claude_permission_mode=os.environ.get('CLAUDE_PERMISSION_MODE', '').strip() or None,
        claude_allowed_tools=_parse_csv(os.environ.get('CLAUDE_ALLOWED_TOOLS')),
        claude_disallowed_tools=_parse_csv(os.environ.get('CLAUDE_DISALLOWED_TOOLS')),
        claude_timeout_seconds=max(1, int(claude_timeout_raw)),
        telegram_poll_timeout=max(1, int(poll_timeout_raw)),
        telegram_edit_interval_seconds=max(0.2, float(edit_interval_raw)),
        telegram_api_base=os.environ.get('TELEGRAM_API_BASE', 'https://api.telegram.org').rstrip('/'),
        session_store_path=store_path,
        status_web_enabled=_parse_bool(os.environ.get('STATUS_WEB_ENABLED'), default=True),
        status_web_host=os.environ.get('STATUS_WEB_HOST', '127.0.0.1').strip() or '127.0.0.1',
        status_web_port=max(1, int(status_web_port_raw)),
        status_web_token=os.environ.get('STATUS_WEB_TOKEN', '').strip() or None,
    )
