from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from approval_state import ApprovalState, PendingApproval
from bridge_runner import BridgeRunner, RunnerError, RunnerResponse
from channel_keys import ConversationRef
from chat_log import ChatLogStore
from claude_runner import format_text_reply
from codex_usage import load_codex_usage
from config import Settings
from media_handler import MediaHandler
from reminder_scheduler import ReminderScheduler, ReminderSchedulerError
from resume_telegram_session import (
    format_resume_target,
    get_resume_target,
    get_resume_targets_for_chat,
)
from runtime_state import BridgeRuntimeState
from runner_factory import build_runner
from session_store import SessionStore
from version_info import get_version_snapshot
from workdir_store import WorkdirStore


LOGGER = logging.getLogger("telegram-claude-bridge.core")

DEFAULT_UI_LANGUAGE = "en"
CHAT_ACTION_INTERVAL_SECONDS = 4.0

UI_TEXT: dict[str, dict[str, str]] = {
    "zh": {
        "provider_call_failed": "{provider} 调用失败:\n{error}",
        "provider_approval_failed": "{provider} 授权续跑失败:\n{error}",
        "no_session_bound": "当前没有绑定会话。",
        "session_status": "当前会话状态:",
        "bridge_health": "Bridge health:",
        "bridge_version": "Bridge version:",
        "project_status": "当前项目目录状态:",
        "session_cleared": "已清除当前会话。",
        "no_session_to_clear": "当前没有可清除的会话。",
        "auto_approval_disabled": "已关闭自动批准，后续权限请求将再次等待 /approve。",
        "no_auto_approval": "当前没有开启自动批准。",
        "approval_denied": "已拒绝本次待授权操作。",
        "no_pending_approval": "当前没有待授权操作。",
        "request_received": "正在调用 {provider}…",
        "request_received_streaming": "正在调用 {provider}…",
        "auto_approval_loop": (
            "检测到相同权限请求被重复触发，已停止自动重试，避免死循环。\n"
            "当前自动批准模式: {mode}\n"
            "这通常表示当前模式不够覆盖所需权限。\n"
            "如果是 git add / git commit 之类的 Bash 权限，请改用 /approve_bypass；\n"
            "如果你不想放开更高权限，发送 /approve_manual 恢复手动确认。"
        ),
        "auto_approval_continues": "检测到权限请求，已按自动批准继续。\nmode: {mode}",
        "permission_request": (
            "检测到 {provider} 在请求文件/工具权限。\n"
            "mode: {mode}\n"
            "发送 /approve 继续这次操作，发送 /deny 取消。\n"
            "如果想当前 chat 后续自动批准编辑权限，发送 /approve_always。\n"
            "如果你连 Bash/git 权限也想自动放行，发送 /approve_bypass。"
        ),
        "auto_approval_enabled": (
            "已开启当前 chat 的自动批准。\n"
            "mode: {mode}\n"
            "后续检测到编辑/写入权限请求时会自动继续。关闭请发送 /approve_manual。"
        ),
        "approval_auto_running": "正在以 {mode} 自动继续执行…",
        "approval_running": "已批准本次操作，正在以 {mode} 继续执行…",
        "project_usage": "用法:\n/project ~/projects/my-new-app\n/project default",
        "project_reset": "已恢复默认项目目录并清除当前会话。",
        "project_reset_default": "当前已在默认项目目录，已清除当前会话。",
        "project_outside_allowed": "项目目录必须位于允许的工作区范围内。\nallowed_roots:\n{allowed_roots}\nrequested: {requested}",
        "project_mkdir_failed": "创建项目目录失败:\n{error}",
        "project_not_directory": "目标不是目录:\n{path}",
        "project_switched": (
            "已切换当前 chat 的项目目录，并清除旧会话。\n"
            "allowed_root: {allowed_root}\n"
            "workdir: {workdir}\n"
            "现在可以直接让机器人在这个目录里开始新项目。"
        ),
        "resume_usage": "用法:\n/resume_local\n/resume_local claude\n/resume_local codex",
        "resume_failed": "生成本地续聊命令失败:\n{error}",
        "resume_none": "当前 chat 没有可恢复的本地会话。",
        "resume_header": "本地继续这个 {channel_label} 会话可用以下命令：",
        "schedule_usage": (
            "用法:\n"
            "/schedule_reminder 2026-04-09 09:00 | 提醒内容\n"
            "/schedule_list\n"
            "/schedule_cancel <id>"
        ),
        "schedule_requires_telegram": "当前只支持在 Telegram chat 中创建提醒。",
        "schedule_parse_failed": "无法解析提醒时间。请使用 YYYY-MM-DD HH:MM 或 YYYY-MM-DDTHH:MM。",
        "schedule_time_past": "提醒时间必须晚于当前时间。",
        "schedule_text_missing": "提醒内容不能为空。",
        "schedule_created": (
            "提醒已创建。\n"
            "id: {id}\n"
            "scheduled_for: {scheduled_for}\n"
            "backend: {backend}\n"
            "text: {text}\n"
            "可用 /schedule_list 查看，或 /schedule_cancel {id} 取消。"
        ),
        "schedule_failed": "创建提醒失败:\n{error}",
        "schedule_list_empty": "当前 chat 没有已安排的提醒。",
        "schedule_list_header": "当前 chat 的提醒列表：",
        "schedule_list_item": (
            "id: {id}\n"
            "scheduled_for: {scheduled_for}\n"
            "status: {status}\n"
            "backend: {backend}\n"
            "text: {text}"
        ),
        "schedule_cancel_usage": "用法:\n/schedule_cancel <id>",
        "schedule_cancel_missing": "没有找到这个提醒 id，或它不属于当前 chat。",
        "schedule_cancelled": "提醒已取消。\nid: {id}",
        "schedule_cancel_failed": "取消提醒失败:\n{error}",
        "help_text": (
            "bot: {bot}\n"
            "{channel_label} 已连接到本机 {provider} 后端。\n"
            "直接发文本即可转发到 {provider}。\n"
            "也支持图片和语音消息。\n"
            "命令: /help /status /health /version /clear /project /project_status /approve /deny "
            "/approve_always /approve_bypass /approve_manual /resume_local "
            "/schedule_reminder /schedule_list /schedule_cancel"
        ),
        "resume_block": "bot: {bot}\nprovider: {provider}\nsession_id: {session_id}\ncwd: {cwd}\ncommand: {command}",
        "unsupported_message_type": "暂不支持这种消息类型。目前支持文本、图片和语音。",
        "image_missing_file_id": "图片消息缺少 file_id。",
        "image_doc_missing_file_id": "图片文件缺少 file_id。",
        "voice_missing_file_id": "语音消息缺少 file_id。",
        "image_received": "正在处理图片…",
        "image_doc_received": "正在处理图片…",
        "image_processing_failed": "图片处理失败:\n{error}",
        "voice_received": "正在转写语音…",
        "voice_transcribed": "正在转交给 {provider}…",
        "voice_processing_failed": "语音处理失败:\n{error}",
        "whatsapp_unsupported_message_type": "暂不支持这种 WhatsApp 消息类型。当前支持文本、图片和音频。",
        "whatsapp_image_missing_media_id": "WhatsApp 图片消息缺少 media id。",
        "whatsapp_audio_missing_media_id": "WhatsApp 音频消息缺少 media id。",
        "whatsapp_image_received": "正在处理 WhatsApp 图片…",
        "whatsapp_image_processing_failed": "WhatsApp 图片处理失败:\n{error}",
        "whatsapp_audio_received": "正在转写 WhatsApp 音频…",
        "whatsapp_voice_transcribed": "正在转交给 {provider}…",
        "whatsapp_audio_processing_failed": "WhatsApp 音频处理失败:\n{error}",
        "desktop_prefix": "[Desktop] {prompt}",
    },
    "de": {
        "provider_call_failed": "{provider}-Aufruf fehlgeschlagen:\n{error}",
        "provider_approval_failed": "{provider}-Fortsetzung nach Freigabe fehlgeschlagen:\n{error}",
        "no_session_bound": "Derzeit ist keine Sitzung verbunden.",
        "session_status": "Aktueller Sitzungsstatus:",
        "bridge_health": "Bridge-Status:",
        "bridge_version": "Bridge-Version:",
        "project_status": "Aktueller Projektverzeichnis-Status:",
        "session_cleared": "Die aktuelle Sitzung wurde gelöscht.",
        "no_session_to_clear": "Es gibt keine Sitzung zum Löschen.",
        "auto_approval_disabled": "Auto-Freigabe wurde deaktiviert. Weitere Anfragen warten wieder auf /approve.",
        "no_auto_approval": "Für diesen Chat ist keine Auto-Freigabe aktiv.",
        "approval_denied": "Die ausstehende Freigabe wurde abgelehnt.",
        "no_pending_approval": "Es gibt keine ausstehende Freigabe.",
        "request_received": "{provider} läuft…",
        "request_received_streaming": "{provider} läuft…",
        "auto_approval_loop": (
            "Dieselbe Berechtigungsanfrage wurde wiederholt ausgelöst. Auto-Wiederholung wurde gestoppt, um eine Schleife zu vermeiden.\n"
            "Aktueller Auto-Freigabemodus: {mode}\n"
            "Das bedeutet meist, dass dieser Modus nicht weit genug reicht.\n"
            "Wenn es um Bash-Berechtigungen wie git add oder git commit geht, verwende /approve_bypass.\n"
            "Wenn du keine breiteren Rechte geben willst, verwende /approve_manual für manuelle Bestätigung."
        ),
        "auto_approval_continues": "Berechtigungsanfrage erkannt. Mit Auto-Freigabe fortgesetzt.\nmode: {mode}",
        "permission_request": (
            "{provider} fordert Datei- oder Tool-Berechtigungen an.\n"
            "mode: {mode}\n"
            "Sende /approve, um fortzufahren, oder /deny, um abzubrechen.\n"
            "Für automatische Freigabe künftiger Edit-/Write-Anfragen in diesem Chat: /approve_always.\n"
            "Für automatische Freigabe auch von Bash/git-Rechten: /approve_bypass."
        ),
        "auto_approval_enabled": (
            "Auto-Freigabe für diesen Chat wurde aktiviert.\n"
            "mode: {mode}\n"
            "Künftige Edit-/Write-Anfragen werden automatisch fortgesetzt. Deaktivieren mit /approve_manual."
        ),
        "approval_auto_running": "Wird jetzt automatisch mit {mode} fortgesetzt…",
        "approval_running": "Freigabe erteilt. Wird jetzt mit {mode} fortgesetzt…",
        "project_usage": "Verwendung:\n/project ~/projects/my-new-app\n/project default",
        "project_reset": "Standard-Projektverzeichnis wurde wiederhergestellt und die aktuelle Sitzung gelöscht.",
        "project_reset_default": "Bereits im Standard-Projektverzeichnis. Die aktuelle Sitzung wurde gelöscht.",
        "project_outside_allowed": "Das Projektverzeichnis muss innerhalb der erlaubten Arbeitsbereiche liegen.\nallowed_roots:\n{allowed_roots}\nrequested: {requested}",
        "project_mkdir_failed": "Projektverzeichnis konnte nicht erstellt werden:\n{error}",
        "project_not_directory": "Ziel ist kein Verzeichnis:\n{path}",
        "project_switched": (
            "Das Projektverzeichnis dieses Chats wurde umgeschaltet und die alte Sitzung gelöscht.\n"
            "allowed_root: {allowed_root}\n"
            "workdir: {workdir}\n"
            "Du kannst jetzt direkt in diesem Verzeichnis mit einem neuen Projekt beginnen."
        ),
        "resume_usage": "Verwendung:\n/resume_local\n/resume_local claude\n/resume_local codex",
        "resume_failed": "Lokaler Resume-Befehl konnte nicht erzeugt werden:\n{error}",
        "resume_none": "Für diesen Chat gibt es keine lokal fortsetzbare Sitzung.",
        "resume_header": "Zum lokalen Fortsetzen dieser {channel_label}-Sitzung kannst du folgende Befehle verwenden:",
        "schedule_usage": (
            "Verwendung:\n"
            "/schedule_reminder 2026-04-09 09:00 | Erinnerungstext\n"
            "/schedule_list\n"
            "/schedule_cancel <id>"
        ),
        "schedule_requires_telegram": "Erinnerungen können derzeit nur in Telegram-Chats erstellt werden.",
        "schedule_parse_failed": "Die Zeit konnte nicht gelesen werden. Verwende YYYY-MM-DD HH:MM oder YYYY-MM-DDTHH:MM.",
        "schedule_time_past": "Die Erinnerungszeit muss in der Zukunft liegen.",
        "schedule_text_missing": "Der Erinnerungstext darf nicht leer sein.",
        "schedule_created": (
            "Erinnerung wurde erstellt.\n"
            "id: {id}\n"
            "scheduled_for: {scheduled_for}\n"
            "backend: {backend}\n"
            "text: {text}\n"
            "Mit /schedule_list anzeigen oder mit /schedule_cancel {id} abbrechen."
        ),
        "schedule_failed": "Erinnerung konnte nicht erstellt werden:\n{error}",
        "schedule_list_empty": "Für diesen Chat sind keine Erinnerungen geplant.",
        "schedule_list_header": "Geplante Erinnerungen für diesen Chat:",
        "schedule_list_item": (
            "id: {id}\n"
            "scheduled_for: {scheduled_for}\n"
            "status: {status}\n"
            "backend: {backend}\n"
            "text: {text}"
        ),
        "schedule_cancel_usage": "Verwendung:\n/schedule_cancel <id>",
        "schedule_cancel_missing": "Diese Erinnerungs-ID wurde nicht gefunden oder gehört nicht zu diesem Chat.",
        "schedule_cancelled": "Erinnerung wurde abgebrochen.\nid: {id}",
        "schedule_cancel_failed": "Erinnerung konnte nicht abgebrochen werden:\n{error}",
        "help_text": (
            "bot: {bot}\n"
            "{channel_label} ist mit dem lokalen {provider}-Backend verbunden.\n"
            "Sende einfach Text, um ihn an {provider} weiterzuleiten.\n"
            "Bilder und Sprachnachrichten werden ebenfalls unterstützt.\n"
            "Befehle: /help /status /health /version /clear /project /project_status /approve /deny "
            "/approve_always /approve_bypass /approve_manual /resume_local "
            "/schedule_reminder /schedule_list /schedule_cancel"
        ),
        "resume_block": "bot: {bot}\nprovider: {provider}\nsession_id: {session_id}\ncwd: {cwd}\ncommand: {command}",
        "unsupported_message_type": "Dieser Nachrichtentyp wird noch nicht unterstützt. Aktuell werden Text, Bilder und Sprachnachrichten unterstützt.",
        "image_missing_file_id": "Der Bildnachricht fehlt eine file_id.",
        "image_doc_missing_file_id": "Der Bilddatei fehlt eine file_id.",
        "voice_missing_file_id": "Der Sprachnachricht fehlt eine file_id.",
        "image_received": "Bild wird verarbeitet…",
        "image_doc_received": "Bild wird verarbeitet…",
        "image_processing_failed": "Bildverarbeitung fehlgeschlagen:\n{error}",
        "voice_received": "Sprachnachricht wird transkribiert…",
        "voice_transcribed": "Wird an {provider} weitergereicht…",
        "voice_processing_failed": "Sprachverarbeitung fehlgeschlagen:\n{error}",
        "whatsapp_unsupported_message_type": "Dieser WhatsApp-Nachrichtentyp wird noch nicht unterstützt. Aktuell werden Text, Bilder und Audio unterstützt.",
        "whatsapp_image_missing_media_id": "Der WhatsApp-Bildnachricht fehlt eine media id.",
        "whatsapp_audio_missing_media_id": "Der WhatsApp-Audionachricht fehlt eine media id.",
        "whatsapp_image_received": "WhatsApp-Bild wird verarbeitet…",
        "whatsapp_image_processing_failed": "WhatsApp-Bildverarbeitung fehlgeschlagen:\n{error}",
        "whatsapp_audio_received": "WhatsApp-Audio wird transkribiert…",
        "whatsapp_voice_transcribed": "Wird an {provider} weitergereicht…",
        "whatsapp_audio_processing_failed": "WhatsApp-Audioverarbeitung fehlgeschlagen:\n{error}",
        "desktop_prefix": "[Desktop] {prompt}",
    },
    "en": {
        "provider_call_failed": "{provider} invocation failed:\n{error}",
        "provider_approval_failed": "{provider} approval continuation failed:\n{error}",
        "no_session_bound": "No session is currently bound.",
        "session_status": "Current session status:",
        "bridge_health": "Bridge health:",
        "bridge_version": "Bridge version:",
        "project_status": "Current project directory status:",
        "session_cleared": "Cleared the current session.",
        "no_session_to_clear": "There is no session to clear.",
        "auto_approval_disabled": "Auto-approval has been disabled. Future permission requests will wait for /approve again.",
        "no_auto_approval": "Auto-approval is not enabled for this chat.",
        "approval_denied": "Denied the pending permission request.",
        "no_pending_approval": "There is no pending permission request.",
        "request_received": "Running {provider}…",
        "request_received_streaming": "Running {provider}…",
        "auto_approval_loop": (
            "The same permission request was triggered repeatedly. Auto-retry has been stopped to avoid a loop.\n"
            "Current auto-approval mode: {mode}\n"
            "This usually means the current mode does not cover the required permissions.\n"
            "If this is a Bash permission such as git add or git commit, use /approve_bypass.\n"
            "If you do not want broader permissions, use /approve_manual to return to manual confirmation."
        ),
        "auto_approval_continues": "Permission request detected. Continued with auto-approval.\nmode: {mode}",
        "permission_request": (
            "{provider} is requesting file or tool permissions.\n"
            "mode: {mode}\n"
            "Send /approve to continue or /deny to cancel.\n"
            "To auto-approve future edit/write requests in this chat, send /approve_always.\n"
            "To also auto-approve Bash/git permissions, send /approve_bypass."
        ),
        "auto_approval_enabled": (
            "Auto-approval has been enabled for this chat.\n"
            "mode: {mode}\n"
            "Future edit/write permission requests will continue automatically. Disable it with /approve_manual."
        ),
        "approval_auto_running": "Continuing automatically with {mode}…",
        "approval_running": "Approval granted. Continuing with {mode}…",
        "project_usage": "Usage:\n/project ~/projects/my-new-app\n/project default",
        "project_reset": "Restored the default project directory and cleared the current session.",
        "project_reset_default": "Already using the default project directory. Cleared the current session.",
        "project_outside_allowed": "The project directory must stay within the allowed workspace roots.\nallowed_roots:\n{allowed_roots}\nrequested: {requested}",
        "project_mkdir_failed": "Failed to create the project directory:\n{error}",
        "project_not_directory": "The target is not a directory:\n{path}",
        "project_switched": (
            "Switched this chat to a new project directory and cleared the old session.\n"
            "allowed_root: {allowed_root}\n"
            "workdir: {workdir}\n"
            "You can now ask the bot to start a new project in this directory."
        ),
        "resume_usage": "Usage:\n/resume_local\n/resume_local claude\n/resume_local codex",
        "resume_failed": "Failed to generate the local resume command:\n{error}",
        "resume_none": "There is no locally resumable session for this chat.",
        "resume_header": "Use the following commands to continue this {channel_label} conversation locally:",
        "schedule_usage": (
            "Usage:\n"
            "/schedule_reminder 2026-04-09 09:00 | Reminder text\n"
            "/schedule_list\n"
            "/schedule_cancel <id>"
        ),
        "schedule_requires_telegram": "Reminders can currently be created only for Telegram chats.",
        "schedule_parse_failed": "Could not parse the reminder time. Use YYYY-MM-DD HH:MM or YYYY-MM-DDTHH:MM.",
        "schedule_time_past": "The reminder time must be in the future.",
        "schedule_text_missing": "Reminder text must not be empty.",
        "schedule_created": (
            "Reminder created.\n"
            "id: {id}\n"
            "scheduled_for: {scheduled_for}\n"
            "backend: {backend}\n"
            "text: {text}\n"
            "Use /schedule_list to inspect it or /schedule_cancel {id} to cancel it."
        ),
        "schedule_failed": "Failed to create the reminder:\n{error}",
        "schedule_list_empty": "There are no scheduled reminders for this chat.",
        "schedule_list_header": "Scheduled reminders for this chat:",
        "schedule_list_item": (
            "id: {id}\n"
            "scheduled_for: {scheduled_for}\n"
            "status: {status}\n"
            "backend: {backend}\n"
            "text: {text}"
        ),
        "schedule_cancel_usage": "Usage:\n/schedule_cancel <id>",
        "schedule_cancel_missing": "That reminder id was not found or does not belong to this chat.",
        "schedule_cancelled": "Cancelled the reminder.\nid: {id}",
        "schedule_cancel_failed": "Failed to cancel the reminder:\n{error}",
        "help_text": (
            "bot: {bot}\n"
            "{channel_label} is connected to the local {provider} backend.\n"
            "Send plain text to forward it to {provider}.\n"
            "Images and voice messages are also supported.\n"
            "Commands: /help /status /health /version /clear /project /project_status /approve /deny "
            "/approve_always /approve_bypass /approve_manual /resume_local "
            "/schedule_reminder /schedule_list /schedule_cancel"
        ),
        "resume_block": "bot: {bot}\nprovider: {provider}\nsession_id: {session_id}\ncwd: {cwd}\ncommand: {command}",
        "unsupported_message_type": "This message type is not supported yet. Supported types are text, images, and voice.",
        "image_missing_file_id": "The image message is missing a file_id.",
        "image_doc_missing_file_id": "The image document is missing a file_id.",
        "voice_missing_file_id": "The voice message is missing a file_id.",
        "image_received": "Processing image…",
        "image_doc_received": "Processing image…",
        "image_processing_failed": "Image processing failed:\n{error}",
        "voice_received": "Transcribing voice…",
        "voice_transcribed": "Sending to {provider}…",
        "voice_processing_failed": "Voice processing failed:\n{error}",
        "whatsapp_unsupported_message_type": "This WhatsApp message type is not supported yet. Supported types are text, images, and audio.",
        "whatsapp_image_missing_media_id": "The WhatsApp image message is missing a media id.",
        "whatsapp_audio_missing_media_id": "The WhatsApp audio message is missing a media id.",
        "whatsapp_image_received": "Processing WhatsApp image…",
        "whatsapp_image_processing_failed": "WhatsApp image processing failed:\n{error}",
        "whatsapp_audio_received": "Transcribing WhatsApp audio…",
        "whatsapp_voice_transcribed": "Sending to {provider}…",
        "whatsapp_audio_processing_failed": "WhatsApp audio processing failed:\n{error}",
        "desktop_prefix": "[Desktop] {prompt}",
    },
}

STATUS_LABELS: dict[str, dict[str, str]] = {
    "zh": {
        "bot": "bot",
        "provider": "provider",
        "channel": "channel",
        "chat_id": "chat_id",
        "workdir": "workdir",
        "cwd": "cwd",
        "streaming": "streaming",
        "project_override": "project_override",
        "pending_approval": "pending_approval",
        "approve_always": "approve_always",
        "session_id": "session_id",
        "updated_at": "updated_at",
        "started_at": "started_at",
        "messages_total": "messages_total",
        "requests_total": "requests_total",
        "active_requests": "active_requests",
        "last_success_at": "last_success_at",
        "last_error_at": "last_error_at",
        "last_error": "last_error",
        "session_count": "session_count",
        "pending_approvals": "pending_approvals",
        "approve_always_chats": "approve_always_chats",
        "status_web": "status_web",
        "default_workdir": "default_workdir",
        "allowed_roots": "allowed_roots",
        "chat_workdir": "chat_workdir",
        "effective_workdir": "effective_workdir",
    },
    "de": {
        "bot": "bot",
        "provider": "provider",
        "channel": "channel",
        "chat_id": "chat_id",
        "workdir": "workdir",
        "cwd": "cwd",
        "streaming": "streaming",
        "project_override": "project_override",
        "pending_approval": "pending_approval",
        "approve_always": "approve_always",
        "session_id": "session_id",
        "updated_at": "updated_at",
        "started_at": "started_at",
        "messages_total": "messages_total",
        "requests_total": "requests_total",
        "active_requests": "active_requests",
        "last_success_at": "last_success_at",
        "last_error_at": "last_error_at",
        "last_error": "last_error",
        "session_count": "session_count",
        "pending_approvals": "pending_approvals",
        "approve_always_chats": "approve_always_chats",
        "status_web": "status_web",
        "default_workdir": "default_workdir",
        "allowed_roots": "allowed_roots",
        "chat_workdir": "chat_workdir",
        "effective_workdir": "effective_workdir",
    },
    "en": {
        "bot": "bot",
        "provider": "provider",
        "channel": "channel",
        "chat_id": "chat_id",
        "workdir": "workdir",
        "cwd": "cwd",
        "streaming": "streaming",
        "project_override": "project_override",
        "pending_approval": "pending_approval",
        "approve_always": "approve_always",
        "session_id": "session_id",
        "updated_at": "updated_at",
        "started_at": "started_at",
        "messages_total": "messages_total",
        "requests_total": "requests_total",
        "active_requests": "active_requests",
        "last_success_at": "last_success_at",
        "last_error_at": "last_error_at",
        "last_error": "last_error",
        "session_count": "session_count",
        "pending_approvals": "pending_approvals",
        "approve_always_chats": "approve_always_chats",
        "status_web": "status_web",
        "default_workdir": "default_workdir",
        "allowed_roots": "allowed_roots",
        "chat_workdir": "chat_workdir",
        "effective_workdir": "effective_workdir",
    },
}

PERMISSION_PATTERNS = (
    re.compile(r"(需要|请求|请|需要先).{0,12}(授权|权限)"),
    re.compile(r"(写入|编辑|修改).{0,12}(README|文件|权限|授权)"),
    re.compile(r"(permission|approval|authorize)", re.IGNORECASE),
    re.compile(r"(write|edit).{0,20}(access|permission)", re.IGNORECASE),
    re.compile(r"(berechtigung|berechtigungen|freigabe|freigeben|genehmig|erlaubnis)", re.IGNORECASE),
)

APPROVAL_CONTINUE_PROMPT = (
    "The user approved the pending file-edit permission request. "
    "Continue the previously blocked task now using the newly granted permissions. "
    "Do not ask again for the same edit permission unless broader access is required."
)

AUTO_APPROVAL_REPEAT_LIMIT = 2


@dataclass(frozen=True)
class SentMessage:
    message_id: str | None = None
    raw: dict | None = None


class BridgeTransport(Protocol):
    can_edit_messages: bool

    def send_message(self, conversation: ConversationRef, text: str, role: str = "system") -> SentMessage | None: ...
    def edit_message(
        self,
        conversation: ConversationRef,
        message_id: str,
        text: str,
        role: str = "system",
    ) -> SentMessage | None: ...
    def help_channel_label(self) -> str: ...


class BridgeCore:
    def __init__(
        self,
        settings: Settings,
        store: SessionStore,
        runner: BridgeRunner,
        media_handler: MediaHandler,
        runtime_state: BridgeRuntimeState,
        version_info: dict[str, str],
        approvals: ApprovalState,
        workdirs: WorkdirStore,
        chat_log: ChatLogStore,
        reminders: ReminderScheduler | None,
        transport: BridgeTransport,
    ) -> None:
        self._settings = settings
        self._store = store
        self._runner = runner
        self._media_handler = media_handler
        self._runtime_state = runtime_state
        self._version_info = version_info
        self._approvals = approvals
        self._workdirs = workdirs
        self._chat_log = chat_log
        self._reminders = reminders
        self._transport = transport
        self._conversation_locks: dict[str, threading.RLock] = {}
        self._conversation_locks_guard = threading.Lock()
        self._conversation_languages: dict[str, str] = {}

    def process_text(self, conversation: ConversationRef, text: str) -> None:
        self.remember_user_language(conversation, text)
        with self._lock_for(conversation):
            self._dispatch_text(conversation, text)

    def _supports_chat_actions(self) -> bool:
        return callable(getattr(self._transport, "send_chat_action", None))

    def _send_chat_action(self, conversation: ConversationRef, action: str = "typing") -> None:
        sender = getattr(self._transport, "send_chat_action", None)
        if callable(sender):
            sender(conversation, action)

    def _start_chat_action_loop(self, conversation: ConversationRef, action: str = "typing"):
        if not self._supports_chat_actions():
            return lambda: None

        stop_event = threading.Event()

        def worker() -> None:
            while not stop_event.is_set():
                try:
                    self._send_chat_action(conversation, action)
                except Exception:
                    LOGGER.debug("Failed to send chat action %s for %s", action, conversation.key, exc_info=True)
                stop_event.wait(CHAT_ACTION_INTERVAL_SECONDS)

        threading.Thread(target=worker, daemon=True).start()
        return stop_event.set

    def remember_user_language(self, conversation: ConversationRef, text: str) -> str:
        previous = self._conversation_languages.get(conversation.key)
        language = self._detect_language(text)
        if text.strip().startswith("/") and language == DEFAULT_UI_LANGUAGE and previous:
            return previous
        self._conversation_languages[conversation.key] = language
        return language

    def render_ui_text(self, conversation: ConversationRef, key: str, **kwargs: object) -> str:
        language = self._conversation_language(conversation)
        template = UI_TEXT.get(language, UI_TEXT[DEFAULT_UI_LANGUAGE]).get(key)
        if template is None:
            template = UI_TEXT[DEFAULT_UI_LANGUAGE][key]
        return template.format(**kwargs)

    def run_prompt(
        self,
        conversation: ConversationRef,
        *,
        prompt: str,
        start_text: str | None,
        image_paths: list[str] | None = None,
    ) -> None:
        if self._settings.claude_streaming:
            self._dispatch_streaming(
                conversation=conversation,
                text=prompt,
                start_text=start_text,
                image_paths=image_paths,
            )
            return

        if start_text:
            self._send_message(conversation, start_text)
        stop_chat_action = self._start_chat_action_loop(conversation)
        self._runtime_state.request_started()

        try:
            record = self._store.get(conversation.key)
            runner = self._runner_for_conversation(conversation)
            workdir = str(self._effective_workdir(conversation))
            if record is None:
                response = runner.ask_new(prompt, image_paths=image_paths)
            else:
                response = runner.ask_resume(record.session_id, prompt, image_paths=image_paths)

            self._store.set(conversation.key, session_id=response.session_id, cwd=workdir)
            for part in format_text_reply(response.text):
                self._send_message(conversation, part, role="assistant")
            self._capture_permission_request(
                conversation=conversation,
                original_prompt=prompt,
                session_id=response.session_id,
                assistant_text=response.text,
            )
            self._runtime_state.request_succeeded()
        except RunnerError as exc:
            LOGGER.exception("Provider invocation failed for conversation %s", conversation.key)
            self._runtime_state.request_failed(str(exc))
            for part in format_text_reply(
                self.render_ui_text(
                    conversation,
                    "provider_call_failed",
                    provider=self._provider_label(),
                    error=exc,
                )
            ):
                self._send_message(conversation, part)
        finally:
            stop_chat_action()

    def build_status_text(self, conversation: ConversationRef) -> str:
        language = self._conversation_language(conversation)
        labels = STATUS_LABELS.get(language, STATUS_LABELS[DEFAULT_UI_LANGUAGE])
        record = self._store.get(conversation.key)
        effective_workdir = self._effective_workdir(conversation)
        project_override = self._workdirs.get(conversation.key)
        base_lines = [
            self.render_ui_text(conversation, "no_session_bound" if record is None else "session_status"),
            f"{labels['bot']}: {self._settings.name}",
            f"{labels['provider']}: {self._provider_label()}",
            f"{labels['channel']}: {conversation.channel}",
            f"{labels['chat_id']}: {conversation.chat_id}",
            f"{labels['workdir']}: {effective_workdir}" if record is None else f"{labels['cwd']}: {record.cwd}",
            f"{labels['streaming']}: {self._bool_word(language, self._settings.claude_streaming)}",
            f"{labels['project_override']}: {project_override or self._off_word(language)}",
            f"{labels['pending_approval']}: {self._yes_no_word(language, self._approvals.get(conversation.key) is not None)}",
            f"{labels['approve_always']}: {self._approvals.get_always_mode(conversation.key) or self._off_word(language)}",
        ]
        if record is not None:
            base_lines.insert(1, f"{labels['session_id']}: {record.session_id}")
            base_lines.insert(3, f"{labels['updated_at']}: {record.updated_at}")

        if self._settings.provider == "codex" and record is not None:
            usage = load_codex_usage(record.session_id)
            if usage is None:
                base_lines.append("codex_usage: unavailable")
            else:
                base_lines.extend(
                    [
                        f"codex_total_tokens: {usage.total_tokens}",
                        f"codex_input_tokens: {usage.input_tokens}",
                        f"codex_cached_input_tokens: {usage.cached_input_tokens}",
                        f"codex_output_tokens: {usage.output_tokens}",
                        f"codex_reasoning_output_tokens: {usage.reasoning_output_tokens}",
                        f"codex_plan: {usage.plan_type or 'unknown'}",
                        f"codex_primary_used_percent: {usage.primary_used_percent if usage.primary_used_percent is not None else 'unknown'}",
                        f"codex_secondary_used_percent: {usage.secondary_used_percent if usage.secondary_used_percent is not None else 'unknown'}",
                    ]
                )

        return "\n".join(base_lines)

    def build_health_text(self, conversation: ConversationRef) -> str:
        snapshot = self._runtime_state.snapshot()
        language = self._conversation_language(conversation)
        labels = STATUS_LABELS[language]
        return (
            f"{UI_TEXT[language]['bridge_health']}\n"
            f"{labels['started_at']}: {snapshot.started_at}\n"
            f"{labels['messages_total']}: {snapshot.messages_total}\n"
            f"{labels['requests_total']}: {snapshot.requests_total}\n"
            f"{labels['active_requests']}: {snapshot.active_requests}\n"
            f"{labels['last_success_at']}: {snapshot.last_success_at or 'none'}\n"
            f"{labels['last_error_at']}: {snapshot.last_error_at or 'none'}\n"
            f"{labels['last_error']}: {snapshot.last_error or 'none'}\n"
            f"{labels['session_count']}: {len(self._store.items())}\n"
            f"{labels['pending_approvals']}: {self._approvals.count()}\n"
            f"{labels['approve_always_chats']}: {self._approvals.always_count()}\n"
            f"{labels['provider']}: {self._provider_label()}\n"
            f"{labels['streaming']}: {self._bool_word(language, self._settings.claude_streaming)}\n"
            f"{labels['status_web']}: {self._on_off_word(language, self._settings.status_web_enabled)}"
        )

    def build_version_text(self, conversation: ConversationRef) -> str:
        language = self._conversation_language(conversation)
        return (
            f"{UI_TEXT[language]['bridge_version']}\n"
            f"provider: {self._version_info['provider']}\n"
            f"git_commit: {self._version_info['git_commit']}\n"
            f"claude_version: {self._version_info['claude_version']}\n"
            f"codex_version: {self._version_info['codex_version']}\n"
            f"copilot_version: {self._version_info['copilot_version']}\n"
            f"transcription_backend: {self._version_info['transcription_backend']}\n"
            f"faster_whisper_version: {self._version_info['faster_whisper_version']}\n"
            f"whisper_bin: {self._version_info['whisper_bin']}\n"
            f"whisper_resolved: {self._version_info['whisper_resolved']}\n"
            f"python: {self._version_info['python']}\n"
            f"platform: {self._version_info['platform']}\n"
            f"claude_bin: {self._version_info['claude_bin']}\n"
            f"codex_bin: {self._version_info['codex_bin']}\n"
            f"copilot_bin: {self._version_info['copilot_bin']}"
        )

    def build_project_status_text(self, conversation: ConversationRef) -> str:
        language = self._conversation_language(conversation)
        labels = STATUS_LABELS.get(language, STATUS_LABELS[DEFAULT_UI_LANGUAGE])
        project_override = self._workdirs.get(conversation.key)
        allowed_roots = [str(path) for path in self._allowed_project_roots()]
        return "\n".join(
            [
                self.render_ui_text(conversation, "project_status"),
                f"{labels['bot']}: {self._settings.name}",
                f"{labels['provider']}: {self._provider_label()}",
                f"{labels['channel']}: {conversation.channel}",
                f"{labels['chat_id']}: {conversation.chat_id}",
                f"{labels['default_workdir']}: {self._settings.claude_workdir}",
                f"{labels['allowed_roots']}: {', '.join(allowed_roots)}",
                f"{labels['chat_workdir']}: {project_override or 'not set'}",
                f"{labels['effective_workdir']}: {self._effective_workdir(conversation)}",
            ]
        )

    def submit_web_prompt(
        self,
        conversation: ConversationRef,
        prompt: str,
        *,
        mirror_to_channel: bool = True,
    ) -> None:
        worker = threading.Thread(
            target=self._run_web_prompt,
            args=(conversation, prompt, mirror_to_channel),
            name=f"web-chat-{conversation.key}",
            daemon=True,
        )
        worker.start()

    def log_message(self, conversation: ConversationRef, *, role: str, source: str, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        self._chat_log.append(
            chat_id=conversation.key,
            channel=conversation.channel,
            role=role,
            source=source,
            text=clean,
        )

    def _dispatch_text(self, conversation: ConversationRef, text: str) -> None:
        if text.startswith("/start"):
            self._send_message(conversation, self._help_text(conversation))
            return
        if text.startswith("/help"):
            self._send_message(conversation, self._help_text(conversation))
            return
        if text.startswith("/status"):
            self._send_message(conversation, self.build_status_text(conversation))
            return
        if text.startswith("/health"):
            self._send_message(conversation, self.build_health_text(conversation))
            return
        if text.startswith("/version"):
            self._send_message(conversation, self.build_version_text(conversation))
            return
        if text.startswith("/clear"):
            self._send_message(
                conversation,
                self.render_ui_text(
                    conversation,
                    "session_cleared" if self._store.clear(conversation.key) else "no_session_to_clear",
                ),
            )
            self._approvals.clear(conversation.key)
            return
        if text.startswith("/project_status"):
            self._send_message(conversation, self.build_project_status_text(conversation))
            return
        if text.startswith("/project"):
            self._dispatch_project_command(conversation, text)
            return
        if text.startswith("/resume_local"):
            self._dispatch_resume_local(conversation, text)
            return
        if text.startswith("/schedule_list"):
            self._dispatch_schedule_list(conversation)
            return
        if text.startswith("/schedule_cancel"):
            self._dispatch_schedule_cancel(conversation, text)
            return
        if text.startswith("/schedule_reminder"):
            self._dispatch_schedule_reminder(conversation, text)
            return
        if text.startswith("/approve_bypass") or text.startswith("/approve-bypass"):
            self._dispatch_set_always_mode(conversation, permission_mode="bypassPermissions", label="bypassPermissions")
            return
        if text.startswith("/approve_always") or text.startswith("/approve-always"):
            self._dispatch_approve_always(conversation)
            return
        if text.startswith("/approve_manual") or text.startswith("/approve-manual"):
            cleared = self._approvals.clear_always_mode(conversation.key)
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "auto_approval_disabled")
                if cleared
                else self.render_ui_text(conversation, "no_auto_approval"),
            )
            return
        if text.startswith("/approve"):
            self._dispatch_approval(conversation)
            return
        if text.startswith("/deny"):
            cleared = self._approvals.clear(conversation.key)
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "approval_denied")
                if cleared
                else self.render_ui_text(conversation, "no_pending_approval"),
            )
            return

        self.log_message(conversation, role="user", source=conversation.channel, text=text)
        self.run_prompt(
            conversation,
            prompt=text,
            start_text=None if self._supports_chat_actions() else self.render_ui_text(
                conversation,
                "request_received",
                provider=self._provider_label(),
            ),
        )

    def _dispatch_streaming(
        self,
        *,
        conversation: ConversationRef,
        text: str,
        start_text: str | None,
        image_paths: list[str] | None = None,
    ) -> None:
        sent = None
        if start_text is not None:
            sent = self._send_message(
                conversation,
                start_text,
            )
        elif not self._supports_chat_actions():
            sent = self._send_message(
                conversation,
                self.render_ui_text(
                    conversation,
                    "request_received_streaming",
                    provider=self._provider_label(),
                ),
            )
        message_id = sent.message_id if sent else None
        record = self._store.get(conversation.key)
        latest_text = ""
        final_session_id = record.session_id if record else None
        last_preview = None
        last_edit_at = 0.0
        stop_chat_action = self._start_chat_action_loop(conversation)
        self._runtime_state.request_started()

        try:
            runner = self._runner_for_conversation(conversation)
            workdir = str(self._effective_workdir(conversation))
            if record is None:
                stream = runner.stream_new(text, image_paths=image_paths)
            else:
                stream = runner.stream_resume(record.session_id, text, image_paths=image_paths)

            for update in stream:
                if update.get("session_id"):
                    final_session_id = update["session_id"]
                if update.get("text"):
                    latest_text = update["text"]

                preview = self._make_live_preview(latest_text)
                now = time.monotonic()
                if preview and message_id is None:
                    stop_chat_action()
                    sent = self._send_message(conversation, preview)
                    message_id = sent.message_id if sent else None
                    last_preview = preview
                    last_edit_at = now
                    continue
                if (
                    self._transport.can_edit_messages
                    and preview
                    and preview != last_preview
                    and message_id is not None
                    and now - last_edit_at >= self._settings.telegram_edit_interval_seconds
                ):
                    stop_chat_action()
                    self._edit_message(conversation, message_id, preview)
                    last_preview = preview
                    last_edit_at = now

            if final_session_id:
                self._store.set(conversation.key, session_id=final_session_id, cwd=workdir)

            parts = format_text_reply(latest_text)
            for part in parts:
                self.log_message(conversation, role="assistant", source="bridge", text=part)
            stop_chat_action()
            if message_id is None or not self._transport.can_edit_messages:
                for part in parts:
                    self._send_message(conversation, part, role="assistant")
            else:
                if parts and parts[0] != last_preview:
                    self._edit_message(conversation, message_id, parts[0], role="assistant")
                for part in parts[1:]:
                    self._send_message(conversation, part, role="assistant")
            self._capture_permission_request(
                conversation=conversation,
                original_prompt=text,
                session_id=final_session_id,
                assistant_text=latest_text,
            )
            self._runtime_state.request_succeeded()
        except RunnerError as exc:
            LOGGER.exception("Provider streaming invocation failed for conversation %s", conversation.key)
            self._runtime_state.request_failed(str(exc))
            error_text = self.render_ui_text(
                conversation,
                "provider_call_failed",
                provider=self._provider_label(),
                error=exc,
            )
            stop_chat_action()
            if message_id is not None and self._transport.can_edit_messages:
                parts = format_text_reply(error_text)
                if parts:
                    self._edit_message(conversation, message_id, parts[0])
                for part in parts[1:]:
                    self._send_message(conversation, part)
            else:
                for part in format_text_reply(error_text):
                    self._send_message(conversation, part)
        finally:
            stop_chat_action()

    def _capture_permission_request(
        self,
        *,
        conversation: ConversationRef,
        original_prompt: str,
        session_id: str | None,
        assistant_text: str,
    ) -> None:
        if not self._looks_like_permission_request(assistant_text):
            self._approvals.clear(conversation.key)
            self._approvals.reset_auto_request(conversation.key)
            return

        always_mode = self._approvals.get_always_mode(conversation.key)
        permission_mode = always_mode or self._settings.claude_approval_permission_mode
        approval = self._approvals.set(
            chat_id=conversation.key,
            channel=conversation.channel,
            session_id=session_id,
            cwd=str(self._effective_workdir(conversation)),
            original_prompt=original_prompt,
            permission_mode=permission_mode,
            assistant_message=assistant_text,
        )
        if always_mode:
            fingerprint = f"{approval.permission_mode}\n{approval.assistant_message.strip()}"
            repeat_count = self._approvals.record_auto_request(conversation.key, fingerprint)
            if repeat_count >= AUTO_APPROVAL_REPEAT_LIMIT:
                self._send_message(
                    conversation,
                    self.render_ui_text(
                        conversation,
                        "auto_approval_loop",
                        mode=approval.permission_mode,
                    ),
                )
                return
            self._send_message(
                conversation,
                self.render_ui_text(
                    conversation,
                    "auto_approval_continues",
                    mode=approval.permission_mode,
                ),
            )
            self._dispatch_approval(conversation, auto_approved=True)
            return

        self._send_message(
            conversation,
            self.render_ui_text(
                conversation,
                "permission_request",
                provider=self._provider_label(),
                mode=approval.permission_mode,
            ),
        )

    def _dispatch_approve_always(self, conversation: ConversationRef) -> None:
        self._dispatch_set_always_mode(
            conversation,
            permission_mode=self._settings.claude_approval_permission_mode,
            label=self._settings.claude_approval_permission_mode,
        )

    def _dispatch_set_always_mode(self, conversation: ConversationRef, *, permission_mode: str, label: str) -> None:
        self._approvals.set_always_mode(conversation.key, permission_mode)
        self._send_message(
            conversation,
            self.render_ui_text(conversation, "auto_approval_enabled", mode=label),
        )
        if self._approvals.get(conversation.key):
            self._dispatch_approval(conversation, auto_approved=True)

    def _dispatch_approval(self, conversation: ConversationRef, *, auto_approved: bool = False) -> None:
        approval = self._approvals.pop(conversation.key)
        if approval is None:
            self._send_message(conversation, self.render_ui_text(conversation, "no_pending_approval"))
            return
        if auto_approved:
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "approval_auto_running", mode=approval.permission_mode),
            )
        else:
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "approval_running", mode=approval.permission_mode),
            )
        self._runtime_state.request_started()

        try:
            response = self._continue_after_approval(approval)
            if response.session_id:
                self._store.set(conversation.key, session_id=response.session_id, cwd=approval.cwd)
            for part in format_text_reply(response.text):
                self._send_message(conversation, part, role="assistant")
            self._capture_permission_request(
                conversation=conversation,
                original_prompt=approval.original_prompt,
                session_id=response.session_id,
                assistant_text=response.text,
            )
            self._runtime_state.request_succeeded()
        except RunnerError as exc:
            LOGGER.exception("Approval continuation failed for conversation %s", conversation.key)
            self._runtime_state.request_failed(str(exc))
            for part in format_text_reply(
                self.render_ui_text(
                    conversation,
                    "provider_approval_failed",
                    provider=self._provider_label(),
                    error=exc,
                )
            ):
                self._send_message(conversation, part)

    def _continue_after_approval(self, approval: PendingApproval) -> RunnerResponse:
        runner = self._runner_for_workdir(Path(approval.cwd))
        if self._settings.claude_streaming:
            if approval.session_id:
                updates = runner.stream_resume(
                    approval.session_id,
                    APPROVAL_CONTINUE_PROMPT,
                    permission_mode_override=approval.permission_mode,
                )
            else:
                updates = runner.stream_new(
                    approval.original_prompt,
                    permission_mode_override=approval.permission_mode,
                )

            latest_text = ""
            final_session_id = approval.session_id
            for update in updates:
                if update.get("session_id"):
                    final_session_id = update["session_id"]
                if update.get("text"):
                    latest_text = update["text"]
            return RunnerResponse(
                session_id=final_session_id or "",
                text=latest_text,
                raw={"type": "approval_stream_result"},
                command=[],
            )

        if approval.session_id:
            return runner.ask_resume(
                approval.session_id,
                APPROVAL_CONTINUE_PROMPT,
                permission_mode_override=approval.permission_mode,
            )
        return runner.ask_new(
            approval.original_prompt,
            permission_mode_override=approval.permission_mode,
        )

    def _dispatch_project_command(self, conversation: ConversationRef, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) == 1 or not parts[1].strip():
            self._send_message(conversation, self.render_ui_text(conversation, "project_usage"))
            return

        raw_target = parts[1].strip()
        if raw_target.lower() in {"default", "reset"}:
            cleared = self._workdirs.clear(conversation.key)
            self._store.clear(conversation.key)
            self._approvals.clear(conversation.key)
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "project_reset")
                if cleared
                else self.render_ui_text(conversation, "project_reset_default"),
            )
            return

        candidate = Path(raw_target).expanduser()
        if not candidate.is_absolute():
            candidate = (self._effective_workdir(conversation) / candidate).resolve()
        else:
            candidate = candidate.resolve()

        matched_root = self._find_allowed_project_root(candidate)
        if matched_root is None:
            allowed_roots = "\n".join(f"- {path}" for path in self._allowed_project_roots())
            self._send_message(
                conversation,
                self.render_ui_text(
                    conversation,
                    "project_outside_allowed",
                    allowed_roots=allowed_roots,
                    requested=candidate,
                ),
            )
            return

        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "project_mkdir_failed", error=exc),
            )
            return
        if not candidate.is_dir():
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "project_not_directory", path=candidate),
            )
            return

        self._workdirs.set(conversation.key, str(candidate))
        self._store.clear(conversation.key)
        self._approvals.clear(conversation.key)
        self._send_message(
            conversation,
            self.render_ui_text(
                conversation,
                "project_switched",
                allowed_root=matched_root,
                workdir=candidate,
            ),
        )

    def _dispatch_resume_local(self, conversation: ConversationRef, text: str) -> None:
        parts = text.split(maxsplit=1)
        provider = parts[1].strip().lower() if len(parts) > 1 and parts[1].strip() else None
        if provider and provider not in {"claude", "codex", "copilot"}:
            self._send_message(conversation, self.render_ui_text(conversation, "resume_usage"))
            return
        try:
            if provider:
                targets = [get_resume_target(chat_id=conversation.key, provider=provider)]
            else:
                targets = get_resume_targets_for_chat(conversation.key)
        except RuntimeError as exc:
            self._send_message(conversation, self.render_ui_text(conversation, "resume_failed", error=exc))
            return
        if not targets:
            self._send_message(conversation, self.render_ui_text(conversation, "resume_none"))
            return
        header = self.render_ui_text(
            conversation,
            "resume_header",
            channel_label=self._transport.help_channel_label(),
        )
        body = "\n\n".join(
            self.render_ui_text(
                conversation,
                "resume_block",
                bot=target.settings.name,
                provider=target.settings.provider,
                session_id=target.record.session_id,
                cwd=target.record.cwd,
                command=format_resume_target(target).split("command: ", 1)[-1],
            )
            for target in targets
        )
        for part in format_text_reply(f"{header}\n\n{body}"):
            self._send_message(conversation, part)

    def _dispatch_schedule_reminder(self, conversation: ConversationRef, text: str) -> None:
        if self._reminders is None:
            self._send_message(
                conversation,
                self.render_ui_text(
                    conversation,
                    "schedule_failed",
                    error="scheduler backend is not configured",
                ),
            )
            return
        if conversation.channel != "telegram":
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_requires_telegram"))
            return

        payload = text[len("/schedule_reminder") :].strip()
        if not payload or "|" not in payload:
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_usage"))
            return

        when_raw, reminder_text = payload.split("|", 1)
        when = self._parse_schedule_time(when_raw.strip())
        if when is None:
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_parse_failed"))
            return

        clean_text = reminder_text.strip()
        if not clean_text:
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_text_missing"))
            return
        if when <= datetime.now().replace(second=0, microsecond=0):
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_time_past"))
            return

        try:
            scheduled = self._reminders.schedule_telegram_reminder(
                conversation=conversation,
                when=when,
                text=clean_text,
            )
        except ReminderSchedulerError as exc:
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "schedule_failed", error=exc),
            )
            return

        self._send_message(
            conversation,
            self.render_ui_text(
                conversation,
                "schedule_created",
                id=scheduled.record.id,
                scheduled_for=scheduled.record.scheduled_for,
                backend=scheduled.record.backend,
                text=scheduled.record.text,
            ),
        )

    def _dispatch_schedule_list(self, conversation: ConversationRef) -> None:
        if self._reminders is None:
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_list_empty"))
            return
        reminders = self._reminders.list_for_conversation(conversation)
        if not reminders:
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_list_empty"))
            return

        body = "\n\n".join(
            self.render_ui_text(
                conversation,
                "schedule_list_item",
                id=record.id,
                scheduled_for=record.scheduled_for,
                status=record.status,
                backend=record.backend,
                text=record.text,
            )
            for record in reminders
        )
        for part in format_text_reply(
            f"{self.render_ui_text(conversation, 'schedule_list_header')}\n\n{body}"
        ):
            self._send_message(conversation, part)

    def _dispatch_schedule_cancel(self, conversation: ConversationRef, text: str) -> None:
        if self._reminders is None:
            self._send_message(
                conversation,
                self.render_ui_text(
                    conversation,
                    "schedule_failed",
                    error="scheduler backend is not configured",
                ),
            )
            return

        parts = text.split(maxsplit=1)
        if len(parts) == 1 or not parts[1].strip():
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_cancel_usage"))
            return

        reminder_id = parts[1].strip()
        record = self._reminders.get(reminder_id)
        if record is None or record.conversation_key != conversation.key:
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_cancel_missing"))
            return

        try:
            cancelled = self._reminders.cancel(reminder_id)
        except ReminderSchedulerError as exc:
            self._send_message(
                conversation,
                self.render_ui_text(conversation, "schedule_cancel_failed", error=exc),
            )
            return
        if cancelled is None:
            self._send_message(conversation, self.render_ui_text(conversation, "schedule_cancel_missing"))
            return
        self._send_message(
            conversation,
            self.render_ui_text(conversation, "schedule_cancelled", id=cancelled.id),
        )

    def _run_web_prompt(self, conversation: ConversationRef, prompt: str, mirror_to_channel: bool) -> None:
        clean = prompt.strip()
        if not clean:
            return
        with self._lock_for(conversation):
            self._runtime_state.record_message()
            self.log_message(conversation, role="user", source="web", text=clean)
            if mirror_to_channel:
                self._transport.send_message(
                    conversation,
                    self.render_ui_text(conversation, "desktop_prefix", prompt=clean),
                    role="system",
                )
            self.run_prompt(conversation, prompt=clean, start_text=None)

    def _effective_workdir(self, conversation: ConversationRef) -> Path:
        override = self._workdirs.get(conversation.key)
        if override:
            return Path(override)
        return self._settings.claude_workdir

    def _runner_for_conversation(self, conversation: ConversationRef) -> BridgeRunner:
        return self._runner_for_workdir(self._effective_workdir(conversation))

    def _runner_for_workdir(self, workdir: Path) -> BridgeRunner:
        return build_runner(replace(self._settings, claude_workdir=workdir))

    def _help_text(self, conversation: ConversationRef) -> str:
        return self.render_ui_text(
            conversation,
            "help_text",
            bot=self._settings.name,
            channel_label=self._transport.help_channel_label(),
            provider=self._provider_label(),
        )

    def _allowed_project_roots(self) -> list[Path]:
        roots = [self._settings.claude_workdir.resolve()]
        for path in self._settings.claude_allowed_workdirs:
            resolved = path.resolve()
            if resolved not in roots:
                roots.append(resolved)
        return roots

    def _find_allowed_project_root(self, candidate: Path) -> Path | None:
        for root in self._allowed_project_roots():
            try:
                candidate.relative_to(root)
                return root
            except ValueError:
                continue
        return None

    def _send_message(self, conversation: ConversationRef, text: str, role: str = "system") -> SentMessage:
        self.log_message(conversation, role=role, source="bridge", text=text)
        return self._transport.send_message(conversation, text, role=role) or SentMessage()

    def _edit_message(self, conversation: ConversationRef, message_id: str, text: str, role: str = "system") -> SentMessage:
        result = self._transport.edit_message(conversation, message_id, text, role=role)
        return result or SentMessage(message_id=message_id)

    def _lock_for(self, conversation: ConversationRef) -> threading.RLock:
        with self._conversation_locks_guard:
            lock = self._conversation_locks.get(conversation.key)
            if lock is None:
                lock = threading.RLock()
                self._conversation_locks[conversation.key] = lock
            return lock

    def _provider_label(self) -> str:
        return self._settings.provider

    def _parse_schedule_time(self, raw: str) -> datetime | None:
        clean = raw.strip()
        for value in (clean, clean.replace("T", " ")):
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
        return None

    def _conversation_language(self, conversation: ConversationRef) -> str:
        remembered = self._conversation_languages.get(conversation.key)
        if remembered:
            return remembered
        for item in reversed(self._chat_log.items(conversation.key, limit=50)):
            if item.role == "user":
                language = self._detect_language(item.text)
                self._conversation_languages[conversation.key] = language
                return language
        return DEFAULT_UI_LANGUAGE

    @staticmethod
    def _detect_language(text: str) -> str:
        clean = text.strip()
        if not clean:
            return DEFAULT_UI_LANGUAGE
        if re.search(r"[\u4e00-\u9fff]", clean):
            return "zh"
        german_markers = (
            r"\b("
            r"wie|ich|bitte|danke|nicht|und|oder|kann|kannst|gib|zugriff|datei|projekt|warum|welche|mir|"
            r"ist|sind|läuft|funktioniert|fehler|heute|morgen|der|die|das|den|dem|des|ein|eine|einen|"
            r"mein|meine|dein|deine|mit|für|vor|nach|über|unter|zum|zur|erinner|erinnern|erinnerung|"
            r"reifen|aufpumpen|prüfen|uhr"
            r")\b"
        )
        if re.search(r"[äöüßÄÖÜ]", clean) or re.search(german_markers, clean, re.IGNORECASE):
            return "de"
        return "en"

    @staticmethod
    def _bool_word(language: str, value: bool) -> str:
        return {
            "zh": "true" if value else "false",
            "de": "true" if value else "false",
            "en": "true" if value else "false",
        }.get(language, "true" if value else "false")

    @staticmethod
    def _yes_no_word(language: str, value: bool) -> str:
        return {
            "zh": "yes" if value else "no",
            "de": "ja" if value else "nein",
            "en": "yes" if value else "no",
        }.get(language, "yes" if value else "no")

    @staticmethod
    def _on_off_word(language: str, value: bool) -> str:
        return {
            "zh": "on" if value else "off",
            "de": "an" if value else "aus",
            "en": "on" if value else "off",
        }.get(language, "on" if value else "off")

    @staticmethod
    def _off_word(language: str) -> str:
        return {
            "zh": "off",
            "de": "aus",
            "en": "off",
        }.get(language, "off")

    @staticmethod
    def _make_live_preview(text: str, limit: int = 3900) -> str:
        clean = text.strip()
        if not clean:
            return ""
        if len(clean) <= limit:
            return clean
        prefix = "[streaming，显示最近内容]\n\n"
        keep = max(256, limit - len(prefix))
        return prefix + clean[-keep:]

    @staticmethod
    def _looks_like_permission_request(text: str) -> bool:
        clean = text.strip()
        if not clean:
            return False
        return any(pattern.search(clean) for pattern in PERMISSION_PATTERNS)
