from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.request import Request, urlopen

from approval_state import ApprovalState
from bridge_core import BridgeCore, SentMessage
from channel_keys import ConversationRef
from chat_log import ChatLogStore
from config import Settings, load_settings
from construction_agent import ConstructionAgentService
from media_handler import MediaHandler
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
        return replace(
            base,
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
            **overrides,
        )

    def test_demo_seed_bootstraps_target_scale(self) -> None:
        overview = self.service.overview()
        self.assertEqual(overview["counts"]["employees"], 20)
        self.assertEqual(overview["counts"]["sites"], 10)
        self.assertEqual(overview["counts"]["requirements"], 10)
        self.assertEqual(overview["counts"]["vehicles"], 10)

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
            chat_log,
            None,
            self.service,
            transport,
        )
        core.process_text(ConversationRef(channel="telegram", chat_id="100"), "/construction overview")
        self.assertTrue(transport.messages)
        self.assertIn("建筑调度总览", transport.messages[-1][2])

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


if __name__ == "__main__":
    unittest.main()
