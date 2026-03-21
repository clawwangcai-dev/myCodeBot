from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from claude_runner import ClaudeRunner, ClaudeRunnerError, format_text_reply
from config import Settings, load_settings
from runtime_state import BridgeRuntimeState
from session_store import SessionStore
from status_web import start_status_server
from version_info import get_version_snapshot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("telegram-claude-bridge")


class TelegramAPIError(RuntimeError):
    pass


class TelegramBot:
    def __init__(
        self,
        settings: Settings,
        store: SessionStore,
        runner: ClaudeRunner,
        runtime_state: BridgeRuntimeState,
        version_info: dict[str, str],
    ) -> None:
        self._settings = settings
        self._store = store
        self._runner = runner
        self._runtime_state = runtime_state
        self._version_info = version_info
        self._offset = 0
        self._chat_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)

    def run_forever(self) -> None:
        LOGGER.info("Starting Telegram polling against %s", self._settings.telegram_api_base)
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

        if not chat_id or not text:
            return

        self._runtime_state.record_message()
        with self._chat_locks[chat_id]:
            self._dispatch_text(chat_id=chat_id, text=text)

    def _dispatch_text(self, chat_id: int, text: str) -> None:
        if text.startswith("/start"):
            self._send_message(
                chat_id,
                "Telegram 已连接到本机 Claude CLI。\n"
                "直接发文本即可转发到 Claude。\n"
                "命令: /status /health /version /clear",
            )
            return

        if text.startswith("/status"):
            record = self._store.get(chat_id)
            if record is None:
                self._send_message(
                    chat_id,
                    "当前没有绑定会话。\n"
                    f"workdir: {self._settings.claude_workdir}\n"
                    f"streaming: {self._settings.claude_streaming}",
                )
            else:
                self._send_message(
                    chat_id,
                    "当前会话状态:\n"
                    f"session_id: {record.session_id}\n"
                    f"cwd: {record.cwd}\n"
                    f"updated_at: {record.updated_at}\n"
                    f"streaming: {self._settings.claude_streaming}",
                )
            return

        if text.startswith("/health"):
            self._send_message(chat_id, self._build_health_text())
            return

        if text.startswith("/version"):
            self._send_message(chat_id, self._build_version_text())
            return

        if text.startswith("/clear"):
            cleared = self._store.clear(chat_id)
            self._send_message(
                chat_id,
                "已清除当前会话。" if cleared else "当前没有可清除的会话。",
            )
            return

        if self._settings.claude_streaming:
            self._dispatch_streaming(chat_id=chat_id, text=text)
            return

        self._send_message(chat_id, "请求已收到，正在调用本机 Claude CLI...")
        self._runtime_state.request_started()

        try:
            record = self._store.get(chat_id)
            if record is None:
                response = self._runner.ask_new(text)
            else:
                response = self._runner.ask_resume(record.session_id, text)

            self._store.set(
                chat_id=chat_id,
                session_id=response.session_id,
                cwd=str(self._settings.claude_workdir),
            )

            for part in format_text_reply(response.text):
                self._send_message(chat_id, part)
            self._runtime_state.request_succeeded()
        except ClaudeRunnerError as exc:
            LOGGER.exception("Claude invocation failed for chat %s", chat_id)
            self._runtime_state.request_failed(str(exc))
            for part in format_text_reply(f"Claude CLI 调用失败:\n{exc}"):
                self._send_message(chat_id, part)

    def _dispatch_streaming(self, chat_id: int, text: str) -> None:
        message = self._send_message(chat_id, "请求已收到，正在流式调用本机 Claude CLI...")
        message_id = message.get("message_id")
        record = self._store.get(chat_id)
        latest_text = ""
        final_session_id = record.session_id if record else None
        last_preview = None
        last_edit_at = 0.0
        self._runtime_state.request_started()

        try:
            if record is None:
                stream = self._runner.stream_new(text)
            else:
                stream = self._runner.stream_resume(record.session_id, text)

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
                    cwd=str(self._settings.claude_workdir),
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
            self._runtime_state.request_succeeded()
        except ClaudeRunnerError as exc:
            LOGGER.exception("Claude streaming invocation failed for chat %s", chat_id)
            self._runtime_state.request_failed(str(exc))
            error_text = f"Claude CLI 调用失败:\n{exc}"
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
            f"streaming: {self._settings.claude_streaming}\n"
            f"status_web: {'on' if self._settings.status_web_enabled else 'off'}"
        )

    def _build_version_text(self) -> str:
        return (
            "Bridge version:\n"
            f"git_commit: {self._version_info['git_commit']}\n"
            f"claude_version: {self._version_info['claude_version']}\n"
            f"python: {self._version_info['python']}\n"
            f"platform: {self._version_info['platform']}\n"
            f"claude_bin: {self._version_info['claude_bin']}"
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


def main() -> None:
    settings = load_settings()
    store = SessionStore(settings.session_store_path)
    runner = ClaudeRunner(settings)
    runtime_state = BridgeRuntimeState()
    version_info = get_version_snapshot(settings)
    if settings.status_web_enabled:
        start_status_server(settings, store, runtime_state, version_info)
    bot = TelegramBot(settings, store, runner, runtime_state, version_info)
    bot.run_forever()


if __name__ == "__main__":
    main()
