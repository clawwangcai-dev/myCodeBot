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
    claude_allowed_workdirs: list[Path]
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
    model_store_path: Path
    effort_store_path: Path
    media_store_path: Path
    claude_model: str | None
    claude_available_models: list[str]
    claude_effort: str | None
    claude_available_efforts: list[str]
    whisper_bin: str
    whisper_model: str
    whisper_fallback_models: list[str]
    whisper_language: str | None
    whisper_threads: int
    codex_bin: str
    codex_model: str | None
    codex_available_models: list[str]
    codex_effort: str | None
    codex_available_efforts: list[str]
    codex_sandbox: str
    codex_approval_policy: str
    copilot_bin: str
    copilot_model: str | None
    copilot_available_models: list[str]
    copilot_use_gh: bool
    status_web_enabled: bool
    status_web_host: str
    status_web_port: int
    status_web_token: str | None
    web_only_mode: bool
    whatsapp_enabled: bool
    whatsapp_verify_token: str | None
    whatsapp_access_token: str | None
    whatsapp_phone_number_id: str | None
    whatsapp_api_base: str
    whatsapp_webhook_host: str
    whatsapp_webhook_port: int
    construction_agent_enabled: bool
    construction_agent_db_path: Path
    construction_agent_seed_path: Path | None
    construction_agent_auto_seed: bool


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


def _resolve_path_list(raw: str | None, *, base_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for item in _parse_csv(raw):
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        paths.append(candidate.resolve())
    return paths


def _build_settings(
    values: Mapping[str, str],
    *,
    base_dir: Path,
    default_name: str,
) -> Settings:
    web_only_mode = _parse_bool(values.get("WEB_ONLY_MODE"), default=False)
    token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and not web_only_mode:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    workdir = _resolve_path(values.get("CLAUDE_WORKDIR"), base_dir=base_dir, default=os.getcwd())
    allowed_workdirs = _resolve_path_list(values.get("CLAUDE_ALLOWED_WORKDIRS"), base_dir=base_dir)
    settings_file_raw = values.get("CLAUDE_SETTINGS_FILE", "").strip()
    settings_file = _resolve_path(settings_file_raw, base_dir=base_dir, default=".") if settings_file_raw else None
    output_format = values.get("CLAUDE_OUTPUT_FORMAT", "json").strip() or "json"
    if output_format != "json":
        raise RuntimeError("CLAUDE_OUTPUT_FORMAT must be json for this bridge")

    poll_timeout_raw = values.get("TELEGRAM_POLL_TIMEOUT", "30").strip() or "30"
    claude_timeout_raw = values.get("CLAUDE_TIMEOUT_SECONDS", "300").strip() or "300"
    edit_interval_raw = values.get("TELEGRAM_EDIT_INTERVAL_SECONDS", "1.0").strip() or "1.0"
    status_web_port_raw = values.get("STATUS_WEB_PORT", "8765").strip() or "8765"
    whatsapp_webhook_port_raw = values.get("WHATSAPP_WEBHOOK_PORT", "8877").strip() or "8877"
    status_web_enabled = _parse_bool(values.get("STATUS_WEB_ENABLED"), default=True)
    whatsapp_enabled = _parse_bool(values.get("WHATSAPP_ENABLED"), default=False)

    if web_only_mode and not status_web_enabled:
        raise RuntimeError("WEB_ONLY_MODE requires STATUS_WEB_ENABLED=true")
    if web_only_mode and whatsapp_enabled:
        raise RuntimeError("WEB_ONLY_MODE is incompatible with WHATSAPP_ENABLED=true")

    return Settings(
        name=values.get("BRIDGE_NAME", "").strip() or default_name,
        provider=values.get("BRIDGE_PROVIDER", "claude").strip().lower() or "claude",
        telegram_bot_token=token,
        claude_bin=values.get("CLAUDE_BIN", "claude").strip() or "claude",
        claude_workdir=workdir,
        claude_allowed_workdirs=allowed_workdirs,
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
        model_store_path=_resolve_path(
            values.get("MODEL_STORE_PATH"),
            base_dir=base_dir,
            default="model_overrides.json",
        ),
        effort_store_path=_resolve_path(
            values.get("EFFORT_STORE_PATH"),
            base_dir=base_dir,
            default="effort_overrides.json",
        ),
        media_store_path=_resolve_path(
            values.get("MEDIA_STORE_PATH"),
            base_dir=base_dir,
            default=str(workdir / ".telegram-media"),
        ),
        claude_model=values.get("CLAUDE_MODEL", "").strip() or None,
        claude_available_models=_parse_csv(values.get("CLAUDE_MODELS")),
        claude_effort=values.get("CLAUDE_EFFORT", "").strip().lower() or None,
        claude_available_efforts=_parse_csv(values.get("CLAUDE_EFFORTS", "low,medium,high,xhigh,max,ultracode")),
        whisper_bin=values.get("WHISPER_BIN", "whisper").strip() or "whisper",
        whisper_model=values.get("WHISPER_MODEL", "base").strip() or "base",
        whisper_fallback_models=_parse_csv(values.get("WHISPER_FALLBACK_MODELS", "tiny")),
        whisper_language=values.get("WHISPER_LANGUAGE", "").strip() or None,
        whisper_threads=max(1, int(values.get("WHISPER_THREADS", "2").strip() or "2")),
        codex_bin=values.get("CODEX_BIN", "codex").strip() or "codex",
        codex_model=values.get("CODEX_MODEL", "").strip() or None,
        codex_available_models=_parse_csv(values.get("CODEX_MODELS")),
        codex_effort=values.get("CODEX_EFFORT", "").strip().lower() or None,
        codex_available_efforts=_parse_csv(values.get("CODEX_EFFORTS", "low,medium,high,extra high")),
        codex_sandbox=values.get("CODEX_SANDBOX", "workspace-write").strip() or "workspace-write",
        codex_approval_policy=values.get("CODEX_APPROVAL_POLICY", "on-request").strip() or "on-request",
        copilot_bin=values.get("COPILOT_BIN", "copilot").strip() or "copilot",
        copilot_model=values.get("COPILOT_MODEL", "").strip() or None,
        copilot_available_models=_parse_csv(values.get("COPILOT_MODELS")),
        copilot_use_gh=_parse_bool(values.get("COPILOT_USE_GH"), default=False),
        status_web_enabled=status_web_enabled,
        status_web_host=values.get("STATUS_WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
        status_web_port=max(1, int(status_web_port_raw)),
        status_web_token=values.get("STATUS_WEB_TOKEN", "").strip() or None,
        web_only_mode=web_only_mode,
        whatsapp_enabled=whatsapp_enabled,
        whatsapp_verify_token=values.get("WHATSAPP_VERIFY_TOKEN", "").strip() or None,
        whatsapp_access_token=values.get("WHATSAPP_ACCESS_TOKEN", "").strip() or None,
        whatsapp_phone_number_id=values.get("WHATSAPP_PHONE_NUMBER_ID", "").strip() or None,
        whatsapp_api_base=(
            values.get("WHATSAPP_API_BASE", "https://graph.facebook.com/v22.0").strip()
            or "https://graph.facebook.com/v22.0"
        ).rstrip("/"),
        whatsapp_webhook_host=values.get("WHATSAPP_WEBHOOK_HOST", "127.0.0.1").strip() or "127.0.0.1",
        whatsapp_webhook_port=max(1, int(whatsapp_webhook_port_raw)),
        construction_agent_enabled=_parse_bool(values.get("CONSTRUCTION_AGENT_ENABLED"), default=False),
        construction_agent_db_path=_resolve_path(
            values.get("CONSTRUCTION_AGENT_DB_PATH"),
            base_dir=base_dir,
            default="construction_agent.sqlite3",
        ),
        construction_agent_seed_path=(
            _resolve_path(values.get("CONSTRUCTION_AGENT_SEED_PATH"), base_dir=base_dir, default="construction_agent_seed.json")
            if values.get("CONSTRUCTION_AGENT_SEED_PATH", "").strip()
            else None
        ),
        construction_agent_auto_seed=_parse_bool(values.get("CONSTRUCTION_AGENT_AUTO_SEED"), default=True),
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
        if "MODEL_STORE_PATH" not in item_values:
            merged["MODEL_STORE_PATH"] = str(bot_data_dir / "model_overrides.json")
        if "EFFORT_STORE_PATH" not in item_values:
            merged["EFFORT_STORE_PATH"] = str(bot_data_dir / "effort_overrides.json")
        if "MEDIA_STORE_PATH" not in item_values:
            merged["MEDIA_STORE_PATH"] = str(bot_data_dir / ".telegram-media")
        if "CONSTRUCTION_AGENT_DB_PATH" not in item_values:
            merged["CONSTRUCTION_AGENT_DB_PATH"] = str(bot_data_dir / "construction_agent.sqlite3")
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
