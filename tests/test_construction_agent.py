from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from approval_state import ApprovalState
from bot import LocalWebBridge
from bridge_core import BridgeCore, SentMessage
from bridge_runner import RunnerResponse
from channel_keys import ConversationRef
from chat_log import ChatLogStore
from config import Settings, load_settings
from construction_agent import ConstructionAgentService
from media_handler import MediaHandler
from model_store import ModelStore
from runtime_state import BridgeRuntimeState
from session_store import SessionStore
from status_web import start_status_server
from workdir_store import WorkdirStore


class DummyRunner:
    def ask_new(self, prompt, image_paths=None):  # pragma: no cover - should never be called
        raise AssertionError(f"Unexpected provider call: {prompt}")

    def ask_resume(self, session_id, prompt, image_paths=None):  # pragma: no cover - should never be called
        raise AssertionError(f"Unexpected provider resume call: {prompt}")

    def stream_new(self, prompt, image_paths=None):  # pragma: no cover - should never be called
        raise AssertionError(f"Unexpected provider stream call: {prompt}")

    def stream_resume(self, session_id, prompt, image_paths=None):  # pragma: no cover - should never be called
        raise AssertionError(f"Unexpected provider stream resume call: {prompt}")


class EchoRunner:
    def ask_new(self, prompt, permission_mode_override=None, image_paths=None):
        return RunnerResponse(
            session_id="echo-session",
            text=f"Echo: {prompt}",
            raw={"type": "echo"},
            command=["echo"],
        )

    def ask_resume(self, session_id, prompt, permission_mode_override=None, image_paths=None):
        return RunnerResponse(
            session_id=session_id,
            text=f"Echo: {prompt}",
            raw={"type": "echo"},
            command=["echo"],
        )

    def stream_new(self, prompt, permission_mode_override=None, image_paths=None):  # pragma: no cover - not used here
        raise AssertionError(f"Unexpected provider stream call: {prompt}")

    def stream_resume(self, session_id, prompt, permission_mode_override=None, image_paths=None):  # pragma: no cover
        raise AssertionError(f"Unexpected provider stream resume call: {prompt}")


class DummyTransport:
    can_edit_messages = False

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_message(self, conversation: ConversationRef, text: str, role: str = "system") -> SentMessage | None:
        self.messages.append((conversation.key, role, text))
        return SentMessage(message_id=str(len(self.messages)))

    def edit_message(
        self,
        conversation: ConversationRef,
        message_id: str,
        text: str,
        role: str = "system",
    ) -> SentMessage | None:
        self.messages.append((conversation.key, role, text))
        return SentMessage(message_id=message_id)

    def help_channel_label(self) -> str:
        return "Test"


class ConstructionAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["TELEGRAM_BOT_TOKEN"] = "unit-test-token"
        os.environ["BRIDGE_PROVIDER"] = "codex"
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.settings = self._make_settings()
        self.service = ConstructionAgentService(self.settings)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_settings(self, **overrides: object) -> Settings:
        base = load_settings()
        defaults = dict(
            status_web_enabled=True,
            status_web_host="127.0.0.1",
            status_web_port=0,
            session_store_path=self.tmp_path / "sessions.json",
            workdir_store_path=self.tmp_path / "chat_workdirs.json",
            approval_store_path=self.tmp_path / "approval_prefs.json",
            media_store_path=self.tmp_path / "media",
            construction_agent_enabled=True,
            construction_agent_db_path=self.tmp_path / "construction.sqlite3",
            construction_agent_seed_path=None,
            construction_agent_auto_seed=True,
        )
        defaults.update(overrides)
        return replace(base, **defaults)

    def test_demo_seed_bootstraps_target_scale(self) -> None:
        overview = self.service.overview()
        self.assertEqual(overview["counts"]["employees"], 20)
        self.assertEqual(overview["counts"]["sites"], 10)
        self.assertEqual(overview["counts"]["requirements"], 10)
        self.assertEqual(overview["counts"]["vehicles"], 10)

    def test_load_settings_allows_missing_telegram_token_in_web_only_mode(self) -> None:
        previous_token = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        previous_web_only = os.environ.get("WEB_ONLY_MODE")
        previous_status_web = os.environ.get("STATUS_WEB_ENABLED")
        try:
            os.environ["WEB_ONLY_MODE"] = "true"
            os.environ["STATUS_WEB_ENABLED"] = "true"
            settings = load_settings()
        finally:
            if previous_token is not None:
                os.environ["TELEGRAM_BOT_TOKEN"] = previous_token
            else:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            if previous_web_only is not None:
                os.environ["WEB_ONLY_MODE"] = previous_web_only
            else:
                os.environ.pop("WEB_ONLY_MODE", None)
            if previous_status_web is not None:
                os.environ["STATUS_WEB_ENABLED"] = previous_status_web
            else:
                os.environ.pop("STATUS_WEB_ENABLED", None)

        self.assertTrue(settings.web_only_mode)
        self.assertEqual(settings.telegram_bot_token, "")

    def test_generate_plan_and_natural_language_query(self) -> None:
        plan = self.service.generate_plan(created_reason="test", created_by="unit")
        self.assertEqual(len(plan["assignments"]), 10)
        reply = self.service.handle_text(
            ConversationRef(channel="telegram", chat_id="100"),
            "谁最适合和老王一起工作",
        )
        self.assertIsNotNone(reply)
        self.assertIn("老王 的推荐搭档", reply or "")

    def test_voice_note_requires_review_and_confirm_updates_vehicle(self) -> None:
        note = self.service.capture_note(
            conversation=ConversationRef(channel="telegram", chat_id="100"),
            text="记录一下，7号车今天刹车不对，先别跑远。",
            source_type="voice",
            audio_path=str(self.tmp_path / "7.ogg"),
        )
        self.assertEqual(note["classification_type"], "vehicle_issue")
        self.assertEqual(note["status"], "pending_review")
        self.service.confirm_note(note["id"], actor="unit")
        vehicles = self.service.list_resources("vehicles")
        vehicle = next(item for item in vehicles if item["vehicle_code"] == "V07")
        self.assertEqual(vehicle["current_status"], "repair")

    def test_bridge_core_handles_construction_queries_without_provider(self) -> None:
        store = SessionStore(self.tmp_path / "sessions.json")
        workdirs = WorkdirStore(self.tmp_path / "chat_workdirs.json")
        models = ModelStore(self.tmp_path / "models.json")
        efforts = ModelStore(self.tmp_path / "efforts.json")
        approvals = ApprovalState(self.tmp_path / "approval_prefs.json")
        chat_log = ChatLogStore(self.tmp_path / "chat_log.json")
        runtime = BridgeRuntimeState()
        transport = DummyTransport()
        core = BridgeCore(
            self.settings,
            store,
            DummyRunner(),
            MediaHandler(self.settings),
            runtime,
            {
                "provider": "codex",
                "git_commit": "test",
                "claude_version": "n/a",
                "codex_version": "n/a",
                "copilot_version": "n/a",
                "transcription_backend": "n/a",
                "faster_whisper_version": "n/a",
                "whisper_bin": "n/a",
                "whisper_resolved": "n/a",
                "python": "test",
                "platform": "test",
                "claude_bin": "n/a",
                "codex_bin": "n/a",
                "copilot_bin": "n/a",
            },
            approvals,
            workdirs,
            models,
            efforts,
            chat_log,
            None,
            self.service,
            transport,
        )
        core.process_text(ConversationRef(channel="telegram", chat_id="100"), "/construction overview")
        self.assertTrue(transport.messages)
        self.assertIn("建筑调度总览", transport.messages[-1][2])

    def test_model_and_effort_switch_preserve_session(self) -> None:
        settings = self._make_settings(
            provider="codex",
            codex_available_models=["gpt-5.5", "gpt-5.4"],
            codex_available_efforts=["low", "high"],
            codex_model="gpt-5.5",
            codex_effort="low",
        )
        store = SessionStore(self.tmp_path / "sessions-model-effort.json")
        workdirs = WorkdirStore(self.tmp_path / "chat_workdirs-model-effort.json")
        models = ModelStore(self.tmp_path / "models-model-effort.json")
        efforts = ModelStore(self.tmp_path / "efforts-model-effort.json")
        approvals = ApprovalState(self.tmp_path / "approval-model-effort.json")
        chat_log = ChatLogStore(self.tmp_path / "chat-model-effort.json")
        transport = DummyTransport()
        conversation = ConversationRef(channel="telegram", chat_id="100")
        store.set(conversation.key, session_id="existing-session", cwd=str(self.tmp_path))
        core = BridgeCore(
            settings,
            store,
            DummyRunner(),
            MediaHandler(settings),
            BridgeRuntimeState(),
            {
                "provider": "codex",
                "git_commit": "test",
                "claude_version": "n/a",
                "codex_version": "n/a",
                "copilot_version": "n/a",
                "transcription_backend": "n/a",
                "faster_whisper_version": "n/a",
                "whisper_bin": "n/a",
                "whisper_resolved": "n/a",
                "python": "test",
                "platform": "test",
                "claude_bin": "n/a",
                "codex_bin": "n/a",
                "copilot_bin": "n/a",
            },
            approvals,
            workdirs,
            models,
            efforts,
            chat_log,
            None,
            self.service,
            transport,
        )

        core.process_text(conversation, "/model gpt-5.4")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")
        core.process_text(conversation, "/model default")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")
        core.process_text(conversation, "/effort high")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")
        core.process_text(conversation, "/effort default")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")

    def test_status_web_construction_api_serves_overview_and_plan(self) -> None:
        store = SessionStore(self.tmp_path / "sessions-status.json")
        workdirs = WorkdirStore(self.tmp_path / "chat_workdirs-status.json")
        approvals = ApprovalState(self.tmp_path / "approval-status.json")
        chat_log = ChatLogStore(self.tmp_path / "chat-status.json")
        runtime = BridgeRuntimeState()
        server = start_status_server(
            self.settings,
            store,
            workdirs,
            approvals,
            runtime,
            {
                "provider": "codex",
                "git_commit": "test",
                "claude_version": "n/a",
                "codex_version": "n/a",
                "copilot_version": "n/a",
                "transcription_backend": "n/a",
                "faster_whisper_version": "n/a",
                "whisper_bin": "n/a",
                "whisper_resolved": "n/a",
                "python": "test",
                "platform": "test",
                "claude_bin": "n/a",
                "codex_bin": "n/a",
                "copilot_bin": "n/a",
            },
            chat_log,
            self.service,
            lambda *args, **kwargs: None,
        )
        try:
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            with urlopen(f"{base_url}/api/construction/overview", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["counts"]["employees"], 20)

            with urlopen(f"{base_url}/construction", timeout=5) as response:
                page = response.read().decode("utf-8")
            self.assertIn("Print Day Report", page)
            self.assertIn("Prepare Override", page)
            self.assertIn("planViewCaption", page)

            request = Request(
                f"{base_url}/api/construction/plan/generate",
                data=json.dumps({"actor": "web-test"}).encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["data"]["assignments"]), 10)
        finally:
            server.shutdown()
            server.server_close()

    def test_status_web_chat_works_in_web_only_mode(self) -> None:
        settings = self._make_settings(
            telegram_bot_token="",
            web_only_mode=True,
            construction_agent_enabled=False,
        )
        store = SessionStore(self.tmp_path / "sessions-web-only.json")
        workdirs = WorkdirStore(self.tmp_path / "chat_workdirs-web-only.json")
        models = ModelStore(self.tmp_path / "models-web-only.json")
        efforts = ModelStore(self.tmp_path / "efforts-web-only.json")
        approvals = ApprovalState(self.tmp_path / "approval-web-only.json")
        chat_log = ChatLogStore(self.tmp_path / "chat-web-only.json")
        runtime = BridgeRuntimeState()
        with patch("bridge_core.build_runner", return_value=EchoRunner()):
            bridge = LocalWebBridge(
                settings,
                store,
                EchoRunner(),
                MediaHandler(settings),
                runtime,
                {
                    "provider": "codex",
                    "git_commit": "test",
                    "claude_version": "n/a",
                    "codex_version": "n/a",
                    "copilot_version": "n/a",
                    "transcription_backend": "n/a",
                    "faster_whisper_version": "n/a",
                    "whisper_bin": "n/a",
                    "whisper_resolved": "n/a",
                    "python": "test",
                    "platform": "test",
                    "claude_bin": "n/a",
                    "codex_bin": "n/a",
                    "copilot_bin": "n/a",
                },
                approvals,
                workdirs,
                models,
                efforts,
                chat_log,
                None,
                None,
            )
            server = start_status_server(
                settings,
                store,
                workdirs,
                approvals,
                runtime,
                {
                    "provider": "codex",
                    "git_commit": "test",
                    "claude_version": "n/a",
                    "codex_version": "n/a",
                    "copilot_version": "n/a",
                    "transcription_backend": "n/a",
                    "faster_whisper_version": "n/a",
                    "whisper_bin": "n/a",
                    "whisper_resolved": "n/a",
                    "python": "test",
                    "platform": "test",
                    "claude_bin": "n/a",
                    "codex_bin": "n/a",
                    "copilot_bin": "n/a",
                },
                chat_log,
                None,
                bridge.submit_web_prompt,
            )
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base_url}/chat", timeout=5) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("web:local", page)
                self.assertIn("remote mirroring unavailable in web-only mode", page)

                request = Request(
                    f"{base_url}/api/chat/send",
                    data=json.dumps(
                        {
                            "conversation_key": "web:local",
                            "prompt": "hello from web",
                            "mirror_to_telegram": True,
                        }
                    ).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["conversation_key"], "web:local")

                deadline = time.time() + 5
                while time.time() < deadline:
                    if len(chat_log.items("web:local", limit=0)) >= 2:
                        break
                    time.sleep(0.05)

                texts = [item.text for item in chat_log.items("web:local", limit=0)]
                self.assertIn("hello from web", texts)
                self.assertIn("Echo: hello from web", texts)
                self.assertNotIn("[Desktop] hello from web", texts)

                with urlopen(f"{base_url}/api/chat?conversation_key=web:local", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["conversation_key"], "web:local")
                self.assertEqual(payload["channel"], "web")

                with urlopen(f"{base_url}/api/status", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["bridge"]["web_only_mode"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
