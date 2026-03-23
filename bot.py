from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from approval_state import ApprovalState, PendingApproval
from bridge_runner import BridgeRunner, RunnerError, RunnerResponse
from claude_runner import format_text_reply
from config import Settings, load_all_settings
from codex_usage import load_codex_usage
from media_handler import DownloadedMedia, MediaHandler, MediaHandlerError
from runtime_state import BridgeRuntimeState
from runner_factory import build_runner
from session_store import SessionStore
from status_web import start_status_server
from version_info import get_version_snapshot
from workdir_store import WorkdirStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("telegram-claude-bridge")

PERMISSION_PATTERNS = (
    re.compile(r"(需要|请求|请|需要先).{0,12}(授权|权限)"),
    re.compile(r"(写入|编辑|修改).{0,12}(README|文件|权限|授权)"),
    re.compile(r"(permission|approval|authorize)", re.IGNORECASE),
    re.compile(r"(write|edit).{0,20}(access|permission)", re.IGNORECASE),
)

APPROVAL_CONTINUE_PROMPT = (
    "The Telegram user approved the pending file-edit permission request. "
    "Continue the previously blocked task now using the newly granted permissions. "
    "Do not ask again for the same edit permission unless broader access is required."
)
AUTO_APPROVAL_REPEAT_LIMIT = 2


class TelegramAPIError(RuntimeError):
    pass


class TelegramBot:
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
    ) -> None:
        self._settings = settings
        self._store = store
        self._runner = runner
        self._media_handler = media_handler
        self._runtime_state = runtime_state
        self._version_info = version_info
        self._approvals = approvals
        self._workdirs = workdirs
        self._offset = 0
        self._chat_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)

    def _provider_label(self) -> str:
        return self._settings.provider

    def _help_text(self) -> str:
        return (
            f"bot: {self._settings.name}\n"
            f"Telegram 已连接到本机 {self._provider_label()} 后端。\n"
            f"直接发文本即可转发到 {self._provider_label()}。\n"
            "也支持图片和语音消息。\n"
            "命令: /help /status /health /version /clear /project /project_status /approve /deny "
            "/approve_always /approve_bypass /approve_manual"
        )

    def _build_status_text(self, chat_id: int) -> str:
        record = self._store.get(chat_id)
        effective_workdir = self._effective_workdir(chat_id)
        project_override = self._workdirs.get(chat_id)
        base_lines = [
            "当前没有绑定会话。"
            if record is None
            else "当前会话状态:",
            f"bot: {self._settings.name}",
            f"provider: {self._provider_label()}",
            f"workdir: {effective_workdir}" if record is None else f"cwd: {record.cwd}",
            f"streaming: {self._settings.claude_streaming}",
            f"project_override: {project_override or 'off'}",
            f"pending_approval: {'yes' if self._approvals.get(chat_id) else 'no'}",
            f"approve_always: {self._approvals.get_always_mode(chat_id) or 'off'}",
        ]
        if record is not None:
            base_lines.insert(1, f"session_id: {record.session_id}")
            base_lines.insert(3, f"updated_at: {record.updated_at}")

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

    def run_forever(self) -> None:
        LOGGER.info("Starting Telegram polling against %s", self._settings.telegram_api_base)
        self._sync_commands()
        while True:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._offset = max(self._offset, update["update_id"] + 1)
                    self._handle_update(update)
            except KeyboardInterrupt:
                raise
            except Exception:
                LOGGER.exception("Polling loop failed")
                time.sleep(2)

    def _get_updates(self) -> list[dict[str, Any]]:
        payload = {
            "timeout": self._settings.telegram_poll_timeout,
            "offset": self._offset,
            "allowed_updates": json.dumps(["message"]),
        }
        response = self._call("getUpdates", payload)
        return response.get("result", [])

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()
        chat_id = chat.get("id")

        if not chat_id:
            return

        self._runtime_state.record_message()
        with self._chat_locks[chat_id]:
            self._dispatch_message(chat_id=chat_id, message=message)

    def _dispatch_message(self, chat_id: int, message: dict[str, Any]) -> None:
        text = (message.get("text") or "").strip()
        if text:
            self._dispatch_text(chat_id=chat_id, text=text)
            return

        photo = message.get("photo") or []
        document = message.get("document") or {}
        voice = message.get("voice") or {}
        audio = message.get("audio") or {}

        if photo:
            self._dispatch_photo(chat_id=chat_id, message=message)
            return

        mime_type = (document.get("mime_type") or "").lower()
        if document and mime_type.startswith("image/"):
            self._dispatch_image_document(chat_id=chat_id, message=message)
            return

        if voice:
            self._dispatch_voice(chat_id=chat_id, message=message, payload=voice)
            return

        if audio:
            self._dispatch_voice(chat_id=chat_id, message=message, payload=audio)
            return

        self._send_message(chat_id, "暂不支持这种消息类型。目前支持文本、图片和语音。")

    def _dispatch_text(self, chat_id: int, text: str) -> None:
        if text.startswith("/start"):
            self._send_message(chat_id, self._help_text())
            return

        if text.startswith("/help"):
            self._send_message(chat_id, self._help_text())
            return

        if text.startswith("/status"):
            self._send_message(chat_id, self._build_status_text(chat_id))
            return

        if text.startswith("/health"):
            self._send_message(chat_id, self._build_health_text())
            return

        if text.startswith("/version"):
            self._send_message(chat_id, self._build_version_text())
            return

        if text.startswith("/clear"):
            self._send_message(
                chat_id,
                "已清除当前会话。" if self._store.clear(chat_id) else "当前没有可清除的会话。",
            )
            self._approvals.clear(chat_id)
            return

        if text.startswith("/project_status"):
            self._send_message(chat_id, self._build_project_status_text(chat_id))
            return

        if text.startswith("/project"):
            self._dispatch_project_command(chat_id, text)
            return

        if text.startswith("/approve_bypass") or text.startswith("/approve-bypass"):
            self._dispatch_set_always_mode(chat_id, permission_mode="bypassPermissions", label="bypassPermissions")
            return

        if text.startswith("/approve_always") or text.startswith("/approve-always"):
            self._dispatch_approve_always(chat_id)
            return

        if text.startswith("/approve_manual") or text.startswith("/approve-manual"):
            cleared = self._approvals.clear_always_mode(chat_id)
            self._send_message(
                chat_id,
                "已关闭自动批准，后续权限请求将再次等待 /approve。"
                if cleared
                else "当前没有开启自动批准。",
            )
            return

        if text.startswith("/approve"):
            self._dispatch_approval(chat_id)
            return

        if text.startswith("/deny"):
            cleared = self._approvals.clear(chat_id)
            self._send_message(
                chat_id,
                "已拒绝本次待授权操作。" if cleared else "当前没有待授权操作。",
            )
            return

        self._run_prompt(
            chat_id=chat_id,
            prompt=text,
            start_text=f"请求已收到，正在调用本机 {self._provider_label()}…",
        )

    def _dispatch_photo(self, chat_id: int, message: dict[str, Any]) -> None:
        photos = message.get("photo") or []
        largest = photos[-1]
        file_id = largest.get("file_id")
        if not file_id:
            self._send_message(chat_id, "图片消息缺少 file_id。")
            return
        caption = (message.get("caption") or "").strip()
        self._send_message(chat_id, f"已收到图片，正在下载并转交给 {self._provider_label()}…")
        try:
            media = self._download_telegram_media(
                file_id=file_id,
                caption=caption,
                mime_type="image/jpeg",
            )
            prompt = self._media_handler.build_image_prompt(media)
            self._run_prompt(
                chat_id=chat_id,
                prompt=prompt,
                start_text=None,
                image_paths=[str(media.path)] if self._settings.provider == "codex" else None,
            )
        except MediaHandlerError as exc:
            self._send_message(chat_id, f"图片处理失败:\n{exc}")

    def _dispatch_image_document(self, chat_id: int, message: dict[str, Any]) -> None:
        document = message.get("document") or {}
        file_id = document.get("file_id")
        if not file_id:
            self._send_message(chat_id, "图片文件缺少 file_id。")
            return
        caption = (message.get("caption") or "").strip()
        self._send_message(chat_id, f"已收到图片文件，正在下载并转交给 {self._provider_label()}…")
        try:
            media = self._download_telegram_media(
                file_id=file_id,
                caption=caption,
                mime_type=document.get("mime_type"),
                original_name=document.get("file_name"),
            )
            prompt = self._media_handler.build_image_prompt(media)
            self._run_prompt(
                chat_id=chat_id,
                prompt=prompt,
                start_text=None,
                image_paths=[str(media.path)] if self._settings.provider == "codex" else None,
            )
        except MediaHandlerError as exc:
            self._send_message(chat_id, f"图片处理失败:\n{exc}")

    def _dispatch_voice(self, chat_id: int, message: dict[str, Any], payload: dict[str, Any]) -> None:
        file_id = payload.get("file_id")
        if not file_id:
            self._send_message(chat_id, "语音消息缺少 file_id。")
            return
        caption = (message.get("caption") or "").strip()
        self._send_message(chat_id, "已收到语音，正在下载并转写…")
        try:
            media = self._download_telegram_media(
                file_id=file_id,
                caption=caption,
                mime_type=payload.get("mime_type") or "audio/ogg",
                original_name=payload.get("file_name"),
            )
            transcript = self._media_handler.transcribe_voice(media)
            self._send_message(chat_id, f"语音已转写，正在转交给 {self._provider_label()}…")
            prompt = self._media_handler.build_voice_prompt(transcript)
            self._run_prompt(chat_id=chat_id, prompt=prompt, start_text=None)
        except MediaHandlerError as exc:
            self._send_message(chat_id, f"语音处理失败:\n{exc}")

    def _download_telegram_media(
        self,
        *,
        file_id: str,
        caption: str,
        mime_type: str | None,
        original_name: str | None = None,
    ) -> DownloadedMedia:
        metadata = self._call("getFile", {"file_id": file_id}).get("result", {})
        file_path = metadata.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise MediaHandlerError(f"Telegram getFile did not return file_path: {metadata}")
        url = f"{self._settings.telegram_api_base}/file/bot{self._settings.telegram_bot_token}/{file_path}"
        local_name = original_name or file_path.rsplit("/", 1)[-1]
        safe_name = local_name.replace("/", "_").replace("\\", "_")
        return self._media_handler.download(
            file_url=url,
            file_id=file_id,
            file_name=safe_name,
            mime_type=mime_type,
            caption=caption,
        )

    def _run_prompt(
        self,
        *,
        chat_id: int,
        prompt: str,
        start_text: str | None,
        image_paths: list[str] | None = None,
    ) -> None:
        if self._settings.claude_streaming:
            self._dispatch_streaming(
                chat_id=chat_id,
                text=prompt,
                start_text=start_text,
                image_paths=image_paths,
            )
            return

        if start_text:
            self._send_message(chat_id, start_text)
        self._runtime_state.request_started()

        try:
            record = self._store.get(chat_id)
            runner = self._runner_for_chat(chat_id)
            workdir = str(self._effective_workdir(chat_id))
            if record is None:
                response = runner.ask_new(prompt, image_paths=image_paths)
            else:
                response = runner.ask_resume(record.session_id, prompt, image_paths=image_paths)

            self._store.set(
                chat_id=chat_id,
                session_id=response.session_id,
                cwd=workdir,
            )
            for part in format_text_reply(response.text):
                self._send_message(chat_id, part)
            self._capture_permission_request(
                chat_id=chat_id,
                original_prompt=prompt,
                session_id=response.session_id,
                assistant_text=response.text,
            )
            self._runtime_state.request_succeeded()
        except RunnerError as exc:
            LOGGER.exception("Provider invocation failed for chat %s", chat_id)
            self._runtime_state.request_failed(str(exc))
            for part in format_text_reply(f"{self._provider_label()} 调用失败:\n{exc}"):
                self._send_message(chat_id, part)

    def _dispatch_streaming(
        self,
        chat_id: int,
        text: str,
        start_text: str | None = None,
        image_paths: list[str] | None = None,
    ) -> None:
        message = self._send_message(
            chat_id,
            start_text or f"请求已收到，正在流式调用本机 {self._provider_label()}…",
        )
        message_id = message.get("message_id")
        record = self._store.get(chat_id)
        latest_text = ""
        final_session_id = record.session_id if record else None
        last_preview = None
        last_edit_at = 0.0
        self._runtime_state.request_started()

        try:
            runner = self._runner_for_chat(chat_id)
            workdir = str(self._effective_workdir(chat_id))
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
                if (
                    preview
                    and preview != last_preview
                    and message_id is not None
                    and now - last_edit_at >= self._settings.telegram_edit_interval_seconds
                ):
                    self._edit_message(chat_id, message_id, preview)
                    last_preview = preview
                    last_edit_at = now

            if final_session_id:
                self._store.set(
                    chat_id=chat_id,
                    session_id=final_session_id,
                    cwd=workdir,
                )

            parts = format_text_reply(latest_text)
            if message_id is None:
                for part in parts:
                    self._send_message(chat_id, part)
            else:
                if parts[0] != last_preview:
                    self._edit_message(chat_id, message_id, parts[0])
                for part in parts[1:]:
                    self._send_message(chat_id, part)
            self._capture_permission_request(
                chat_id=chat_id,
                original_prompt=text,
                session_id=final_session_id,
                assistant_text=latest_text,
            )
            self._runtime_state.request_succeeded()
        except RunnerError as exc:
            LOGGER.exception("Provider streaming invocation failed for chat %s", chat_id)
            self._runtime_state.request_failed(str(exc))
            error_text = f"{self._provider_label()} 调用失败:\n{exc}"
            if message_id is not None:
                parts = format_text_reply(error_text)
                self._edit_message(chat_id, message_id, parts[0])
                for part in parts[1:]:
                    self._send_message(chat_id, part)
            else:
                for part in format_text_reply(error_text):
                    self._send_message(chat_id, part)

    def _build_health_text(self) -> str:
        snapshot = self._runtime_state.snapshot()
        return (
            "Bridge health:\n"
            f"started_at: {snapshot.started_at}\n"
            f"messages_total: {snapshot.messages_total}\n"
            f"requests_total: {snapshot.requests_total}\n"
            f"active_requests: {snapshot.active_requests}\n"
            f"last_success_at: {snapshot.last_success_at or 'none'}\n"
            f"last_error_at: {snapshot.last_error_at or 'none'}\n"
            f"last_error: {snapshot.last_error or 'none'}\n"
            f"session_count: {len(self._store.items())}\n"
            f"pending_approvals: {self._approvals.count()}\n"
            f"approve_always_chats: {self._approvals.always_count()}\n"
            f"provider: {self._provider_label()}\n"
            f"streaming: {self._settings.claude_streaming}\n"
            f"status_web: {'on' if self._settings.status_web_enabled else 'off'}"
        )

    def _build_version_text(self) -> str:
        return (
            "Bridge version:\n"
            f"provider: {self._version_info['provider']}\n"
            f"git_commit: {self._version_info['git_commit']}\n"
            f"claude_version: {self._version_info['claude_version']}\n"
            f"codex_version: {self._version_info['codex_version']}\n"
            f"copilot_version: {self._version_info['copilot_version']}\n"
            f"whisper_bin: {self._version_info['whisper_bin']}\n"
            f"whisper_resolved: {self._version_info['whisper_resolved']}\n"
            f"python: {self._version_info['python']}\n"
            f"platform: {self._version_info['platform']}\n"
            f"claude_bin: {self._version_info['claude_bin']}\n"
            f"codex_bin: {self._version_info['codex_bin']}\n"
            f"copilot_bin: {self._version_info['copilot_bin']}"
        )

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

    def _capture_permission_request(
        self,
        *,
        chat_id: int,
        original_prompt: str,
        session_id: str | None,
        assistant_text: str,
    ) -> None:
        if not self._looks_like_permission_request(assistant_text):
            self._approvals.clear(chat_id)
            self._approvals.reset_auto_request(chat_id)
            return

        always_mode = self._approvals.get_always_mode(chat_id)
        permission_mode = always_mode or self._settings.claude_approval_permission_mode
        approval = self._approvals.set(
            chat_id=chat_id,
            session_id=session_id,
            cwd=str(self._effective_workdir(chat_id)),
            original_prompt=original_prompt,
            permission_mode=permission_mode,
            assistant_message=assistant_text,
        )
        if always_mode:
            fingerprint = f"{approval.permission_mode}\n{approval.assistant_message.strip()}"
            repeat_count = self._approvals.record_auto_request(chat_id, fingerprint)
            if repeat_count >= AUTO_APPROVAL_REPEAT_LIMIT:
                self._send_message(
                    chat_id,
                    "检测到相同权限请求被重复触发，已停止自动重试，避免死循环。\n"
                    f"当前自动批准模式: {approval.permission_mode}\n"
                    "这通常表示当前模式不够覆盖所需权限。"
                    "\n如果是 git add / git commit 之类的 Bash 权限，请改用 /approve_bypass；"
                    "\n如果你不想放开更高权限，发送 /approve_manual 恢复手动确认。",
                )
                return
            self._send_message(
                chat_id,
                f"检测到权限请求，已按自动批准继续。\nmode: {approval.permission_mode}",
            )
            self._dispatch_approval(chat_id, auto_approved=True)
            return

        self._send_message(
            chat_id,
            f"检测到 {self._provider_label()} 在请求文件/工具权限。\n"
            f"mode: {approval.permission_mode}\n"
            "发送 /approve 继续这次操作，发送 /deny 取消。\n"
            "如果想当前 chat 后续自动批准编辑权限，发送 /approve_always。\n"
            "如果你连 Bash/git 权限也想自动放行，发送 /approve_bypass。",
        )

    def _dispatch_approve_always(self, chat_id: int) -> None:
        self._dispatch_set_always_mode(
            chat_id,
            permission_mode=self._settings.claude_approval_permission_mode,
            label=self._settings.claude_approval_permission_mode,
        )

    def _dispatch_set_always_mode(self, chat_id: int, *, permission_mode: str, label: str) -> None:
        self._approvals.set_always_mode(chat_id, permission_mode)
        self._send_message(
            chat_id,
            "已开启当前 chat 的自动批准。\n"
            f"mode: {label}\n"
            "后续检测到编辑/写入权限请求时会自动继续。关闭请发送 /approve_manual。",
        )
        if self._approvals.get(chat_id):
            self._dispatch_approval(chat_id, auto_approved=True)

    def _dispatch_approval(self, chat_id: int, *, auto_approved: bool = False) -> None:
        approval = self._approvals.pop(chat_id)
        if approval is None:
            self._send_message(chat_id, "当前没有待授权操作。")
            return

        if auto_approved:
            self._send_message(chat_id, f"正在以 {approval.permission_mode} 自动继续执行…")
        else:
            self._send_message(
                chat_id,
                f"已批准本次操作，正在以 {approval.permission_mode} 继续执行…",
            )
        self._runtime_state.request_started()

        try:
            response = self._continue_after_approval(approval)
            if response.session_id:
                self._store.set(
                    chat_id=chat_id,
                    session_id=response.session_id,
                    cwd=approval.cwd,
                )
            for part in format_text_reply(response.text):
                self._send_message(chat_id, part)
            self._capture_permission_request(
                chat_id=chat_id,
                original_prompt=approval.original_prompt,
                session_id=response.session_id,
                assistant_text=response.text,
            )
            self._runtime_state.request_succeeded()
        except RunnerError as exc:
            LOGGER.exception("Approval continuation failed for chat %s", chat_id)
            self._runtime_state.request_failed(str(exc))
            for part in format_text_reply(f"{self._provider_label()} 授权续跑失败:\n{exc}"):
                self._send_message(chat_id, part)

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

    def _effective_workdir(self, chat_id: int) -> Path:
        override = self._workdirs.get(chat_id)
        if override:
            return Path(override)
        return self._settings.claude_workdir

    def _runner_for_chat(self, chat_id: int) -> BridgeRunner:
        return self._runner_for_workdir(self._effective_workdir(chat_id))

    def _runner_for_workdir(self, workdir: Path) -> BridgeRunner:
        return build_runner(replace(self._settings, claude_workdir=workdir))

    def _build_project_status_text(self, chat_id: int) -> str:
        project_override = self._workdirs.get(chat_id)
        return "\n".join(
            [
            "当前项目目录状态:",
            f"bot: {self._settings.name}",
            f"provider: {self._provider_label()}",
            f"default_workdir: {self._settings.claude_workdir}",
            f"chat_workdir: {project_override or 'not set'}",
                f"effective_workdir: {self._effective_workdir(chat_id)}",
            ]
        )

    def _dispatch_project_command(self, chat_id: int, text: str) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            self._send_message(
                chat_id,
                "用法:\n/project ~/projects/my-new-app\n/project default",
            )
            return

        raw_target = parts[1].strip()
        if not raw_target:
            self._send_message(
                chat_id,
                "用法:\n/project ~/projects/my-new-app\n/project default",
            )
            return

        if raw_target.lower() in {"default", "reset"}:
            cleared = self._workdirs.clear(chat_id)
            self._store.clear(chat_id)
            self._approvals.clear(chat_id)
            self._send_message(
                chat_id,
                "已恢复默认项目目录并清除当前会话。"
                if cleared
                else "当前已在默认项目目录，已清除当前会话。",
            )
            return

        base_workdir = self._settings.claude_workdir.resolve()
        candidate = Path(raw_target).expanduser()
        if not candidate.is_absolute():
            candidate = (self._effective_workdir(chat_id) / candidate).resolve()
        else:
            candidate = candidate.resolve()

        try:
            candidate.relative_to(base_workdir)
        except ValueError:
            self._send_message(
                chat_id,
                "项目目录必须位于默认工作区范围内。\n"
                f"allowed_root: {base_workdir}\n"
                f"requested: {candidate}",
            )
            return

        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._send_message(chat_id, f"创建项目目录失败:\n{exc}")
            return

        if not candidate.is_dir():
            self._send_message(chat_id, f"目标不是目录:\n{candidate}")
            return

        self._workdirs.set(chat_id, str(candidate))
        self._store.clear(chat_id)
        self._approvals.clear(chat_id)
        self._send_message(
            chat_id,
            "已切换当前 chat 的项目目录，并清除旧会话。\n"
            f"workdir: {candidate}\n"
            "现在可以直接让机器人在这个目录里开始新项目。",
        )

    def _send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        payload = {
            "chat_id": str(chat_id),
            "text": text,
        }
        response = self._call("sendMessage", payload)
        return response.get("result", {})

    def _edit_message(self, chat_id: int, message_id: int, text: str) -> dict[str, Any]:
        payload = {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "text": text,
        }
        response = self._call("editMessageText", payload)
        return response.get("result", {})

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(payload).encode("utf-8")
        url = f"{self._settings.telegram_api_base}/bot{self._settings.telegram_bot_token}/{method}"
        request = Request(url, data=query, method="POST")
        try:
            with urlopen(request, timeout=self._settings.telegram_poll_timeout + 10) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramAPIError(f"Telegram HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise TelegramAPIError(f"Telegram request failed: {exc}") from exc

        data = json.loads(raw)
        if not data.get("ok"):
            raise TelegramAPIError(f"Telegram API returned error: {data}")
        return data

    def _sync_commands(self) -> None:
        commands = [
            {"command": "start", "description": "Start bridge"},
            {"command": "help", "description": "Show bot help"},
            {"command": "status", "description": "Show chat status"},
            {"command": "health", "description": "Show bridge health"},
            {"command": "version", "description": "Show version info"},
            {"command": "clear", "description": "Clear current session"},
            {"command": "project", "description": "Set per-chat project directory"},
            {"command": "project_status", "description": "Show current project directory"},
            {"command": "approve", "description": "Approve pending request"},
            {"command": "approve_always", "description": "Always auto-approve in this chat"},
            {"command": "approve_bypass", "description": "Auto-approve broader Bash/git access"},
            {"command": "approve_manual", "description": "Turn off auto-approve"},
            {"command": "deny", "description": "Deny pending request"},
        ]
        try:
            for scope in (
                {"type": "default"},
                {"type": "all_private_chats"},
            ):
                self._call(
                    "setMyCommands",
                    {
                        "scope": json.dumps(scope, ensure_ascii=False),
                        "commands": json.dumps(commands, ensure_ascii=False),
                    },
                )
        except TelegramAPIError:
            LOGGER.exception("Failed to sync Telegram bot commands")


def main() -> None:
    settings_list = load_all_settings()
    if len(settings_list) == 1:
        _run_single_bot(settings_list[0])
        return

    threads: list[threading.Thread] = []
    for settings in settings_list:
        thread = threading.Thread(
            target=_run_single_bot,
            args=(settings,),
            name=f"telegram-bridge-{settings.name}",
            daemon=False,
        )
        thread.start()
        threads.append(thread)
        LOGGER.info(
            "Started bot worker name=%s provider=%s status_web=%s:%s enabled=%s",
            settings.name,
            settings.provider,
            settings.status_web_host,
            settings.status_web_port,
            settings.status_web_enabled,
        )

    for thread in threads:
        thread.join()


def _run_single_bot(settings: Settings) -> None:
    store = SessionStore(settings.session_store_path)
    workdirs = WorkdirStore(settings.workdir_store_path)
    runner = build_runner(settings)
    media_handler = MediaHandler(settings)
    runtime_state = BridgeRuntimeState()
    version_info = get_version_snapshot(settings)
    approvals = ApprovalState(settings.approval_store_path)
    if settings.status_web_enabled:
        start_status_server(settings, store, workdirs, approvals, runtime_state, version_info)
    bot = TelegramBot(settings, store, runner, media_handler, runtime_state, version_info, approvals, workdirs)
    bot.run_forever()


if __name__ == "__main__":
    main()
