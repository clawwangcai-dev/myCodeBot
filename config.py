from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    name: str
    provider: str
    telegram_bot_token: str
    claude_bin: str
    claude_workdir: Path
    claude_settings_file: Path | None
    claude_output_format: str
    claude_streaming: bool
    claude_permission_mode: str | None
    claude_approval_permission_mode: str
    claude_allowed_tools: list[str]
    claude_disallowed_tools: list[str]
    claude_timeout_seconds: int
    telegram_poll_timeout: int
    telegram_edit_interval_seconds: float
    telegram_api_base: str
    session_store_path: Path
    workdir_store_path: Path
    approval_store_path: Path
    media_store_path: Path
    whisper_bin: str
    whisper_model: str
    whisper_fallback_models: list[str]
    whisper_language: str | None
    whisper_threads: int
    codex_bin: str
    codex_model: str | None
    codex_sandbox: str
    codex_approval_policy: str
    copilot_bin: str
    copilot_model: str | None
    copilot_use_gh: bool
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


def _resolve_path(raw: str | None, *, base_dir: Path, default: str) -> Path:
    candidate = Path((raw or default).strip() or default).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def _build_settings(
    values: Mapping[str, str],
    *,
    base_dir: Path,
    default_name: str,
) -> Settings:
    token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    workdir = _resolve_path(values.get("CLAUDE_WORKDIR"), base_dir=base_dir, default=os.getcwd())
    settings_file_raw = values.get("CLAUDE_SETTINGS_FILE", "").strip()
    settings_file = _resolve_path(settings_file_raw, base_dir=base_dir, default=".") if settings_file_raw else None
    output_format = values.get("CLAUDE_OUTPUT_FORMAT", "json").strip() or "json"
    if output_format != "json":
        raise RuntimeError("CLAUDE_OUTPUT_FORMAT must be json for this bridge")

    poll_timeout_raw = values.get("TELEGRAM_POLL_TIMEOUT", "30").strip() or "30"
    claude_timeout_raw = values.get("CLAUDE_TIMEOUT_SECONDS", "300").strip() or "300"
    edit_interval_raw = values.get("TELEGRAM_EDIT_INTERVAL_SECONDS", "1.0").strip() or "1.0"
    status_web_port_raw = values.get("STATUS_WEB_PORT", "8765").strip() or "8765"

    return Settings(
        name=values.get("BRIDGE_NAME", "").strip() or default_name,
        provider=values.get("BRIDGE_PROVIDER", "claude").strip().lower() or "claude",
        telegram_bot_token=token,
        claude_bin=values.get("CLAUDE_BIN", "claude").strip() or "claude",
        claude_workdir=workdir,
        claude_settings_file=settings_file,
        claude_output_format=output_format,
        claude_streaming=_parse_bool(values.get("CLAUDE_STREAMING"), default=False),
        claude_permission_mode=values.get("CLAUDE_PERMISSION_MODE", "").strip() or None,
        claude_approval_permission_mode=(
            values.get("CLAUDE_APPROVAL_PERMISSION_MODE", "acceptEdits").strip()
            or "acceptEdits"
        ),
        claude_allowed_tools=_parse_csv(values.get("CLAUDE_ALLOWED_TOOLS")),
        claude_disallowed_tools=_parse_csv(values.get("CLAUDE_DISALLOWED_TOOLS")),
        claude_timeout_seconds=max(1, int(claude_timeout_raw)),
        telegram_poll_timeout=max(1, int(poll_timeout_raw)),
        telegram_edit_interval_seconds=max(0.2, float(edit_interval_raw)),
        telegram_api_base=values.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
        session_store_path=_resolve_path(values.get("SESSION_STORE_PATH"), base_dir=base_dir, default="sessions.json"),
        workdir_store_path=_resolve_path(
            values.get("WORKDIR_STORE_PATH"),
            base_dir=base_dir,
            default="chat_workdirs.json",
        ),
        approval_store_path=_resolve_path(
            values.get("APPROVAL_STORE_PATH"),
            base_dir=base_dir,
            default="approval_prefs.json",
        ),
        media_store_path=_resolve_path(
            values.get("MEDIA_STORE_PATH"),
            base_dir=base_dir,
            default=str(workdir / ".telegram-media"),
        ),
        whisper_bin=values.get("WHISPER_BIN", "whisper").strip() or "whisper",
        whisper_model=values.get("WHISPER_MODEL", "base").strip() or "base",
        whisper_fallback_models=_parse_csv(values.get("WHISPER_FALLBACK_MODELS", "tiny")),
        whisper_language=values.get("WHISPER_LANGUAGE", "").strip() or None,
        whisper_threads=max(1, int(values.get("WHISPER_THREADS", "2").strip() or "2")),
        codex_bin=values.get("CODEX_BIN", "codex").strip() or "codex",
        codex_model=values.get("CODEX_MODEL", "").strip() or None,
        codex_sandbox=values.get("CODEX_SANDBOX", "workspace-write").strip() or "workspace-write",
        codex_approval_policy=values.get("CODEX_APPROVAL_POLICY", "on-request").strip() or "on-request",
        copilot_bin=values.get("COPILOT_BIN", "copilot").strip() or "copilot",
        copilot_model=values.get("COPILOT_MODEL", "").strip() or None,
        copilot_use_gh=_parse_bool(values.get("COPILOT_USE_GH"), default=False),
        status_web_enabled=_parse_bool(values.get("STATUS_WEB_ENABLED"), default=True),
        status_web_host=values.get("STATUS_WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
        status_web_port=max(1, int(status_web_port_raw)),
        status_web_token=values.get("STATUS_WEB_TOKEN", "").strip() or None,
    )


def load_settings() -> Settings:
    base_dir = Path(__file__).resolve().parent
    return _build_settings(dict(os.environ), base_dir=base_dir, default_name="default")


def load_all_settings() -> list[Settings]:
    bots_config_raw = os.environ.get("BOTS_CONFIG_FILE", "").strip()
    if not bots_config_raw:
        return [load_settings()]

    bots_config_path = Path(bots_config_raw).expanduser().resolve()
    raw = json.loads(bots_config_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        bot_items = raw.get("bots")
    else:
        bot_items = raw

    if not isinstance(bot_items, list) or not bot_items:
        raise RuntimeError(f"BOTS_CONFIG_FILE must contain a non-empty bot list: {bots_config_path}")

    base_values = dict(os.environ)
    settings_list: list[Settings] = []
    used_names: set[str] = set()
    used_tokens: set[str] = set()
    config_base_dir = bots_config_path.parent

    for index, item in enumerate(bot_items, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Bot config entry #{index} must be an object")

        item_values = {str(key): str(value) for key, value in item.items() if value is not None}
        merged = base_values.copy()
        merged.update(item_values)
        bot_name = merged.get("BRIDGE_NAME", "").strip() or f"bot{index}"
        if bot_name in used_names:
            raise RuntimeError(f"Duplicate bot name in BOTS_CONFIG_FILE: {bot_name}")
        used_names.add(bot_name)

        bot_data_dir = config_base_dir / "data" / bot_name
        if "SESSION_STORE_PATH" not in item_values:
            merged["SESSION_STORE_PATH"] = str(bot_data_dir / "sessions.json")
        if "WORKDIR_STORE_PATH" not in item_values:
            merged["WORKDIR_STORE_PATH"] = str(bot_data_dir / "chat_workdirs.json")
        if "APPROVAL_STORE_PATH" not in item_values:
            merged["APPROVAL_STORE_PATH"] = str(bot_data_dir / "approval_prefs.json")
        if "MEDIA_STORE_PATH" not in item_values:
            merged["MEDIA_STORE_PATH"] = str(bot_data_dir / ".telegram-media")
        if "STATUS_WEB_ENABLED" not in item_values:
            merged["STATUS_WEB_ENABLED"] = "false"

        settings = _build_settings(merged, base_dir=config_base_dir, default_name=bot_name)
        if settings.telegram_bot_token in used_tokens:
            raise RuntimeError(
                "Duplicate TELEGRAM_BOT_TOKEN in BOTS_CONFIG_FILE: "
                f"{settings.name}"
            )
        used_tokens.add(settings.telegram_bot_token)
        settings_list.append(settings)

    _validate_status_web_conflicts(settings_list)
    return settings_list


def _validate_status_web_conflicts(settings_list: list[Settings]) -> None:
    bound: set[tuple[str, int]] = set()
    for settings in settings_list:
        if not settings.status_web_enabled:
            continue
        key = (settings.status_web_host, settings.status_web_port)
        if key in bound:
            raise RuntimeError(
                "Multiple bots cannot share the same status web host/port: "
                f"{settings.status_web_host}:{settings.status_web_port}"
            )
        bound.add(key)
