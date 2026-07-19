from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from codex_runner import CodexRunner, CodexRunnerError
from config import Settings, load_settings


class CodexRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["TELEGRAM_BOT_TOKEN"] = "unit-test-token"
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.settings = self._make_settings()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_settings(self, **overrides: object) -> Settings:
        defaults = dict(
            provider="codex",
            claude_workdir=self.tmp_path,
            codex_model="gpt5.6",
            claude_streaming=False,
        )
        defaults.update(overrides)
        return replace(load_settings(), **defaults)

    def test_run_error_includes_codex_json_stdout_error(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "test-session"}),
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            '{"type":"error","status":400,"error":{"message":'
                            '"The gpt5.6 model is not supported"}}'
                        ),
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {
                            "message": (
                                '{"type":"error","status":400,"error":{"message":'
                                '"The gpt5.6 model is not supported"}}'
                            ),
                        },
                    }
                ),
            ]
        )
        completed = subprocess.CompletedProcess(
            args=["codex"],
            returncode=1,
            stdout=stdout,
            stderr="",
        )

        with patch("codex_runner.subprocess.run", return_value=completed):
            with self.assertRaises(CodexRunnerError) as raised:
                CodexRunner(self.settings).ask_new("hello")

        message = str(raised.exception)
        self.assertIn("The gpt5.6 model is not supported", message)
        self.assertIn("codex_json_errors:", message)

    def test_stream_error_includes_codex_json_stdout_error(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "test-session"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "error",
                            "message": "Model metadata for `gpt5.6` not found.",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            '{"type":"error","status":400,"error":{"message":'
                            '"The gpt5.6 model is not supported"}}'
                        ),
                    }
                ),
            ]
        )
        process = _FakeProcess(stdout=stdout, stderr="", returncode=1)

        with patch("codex_runner.subprocess.Popen", return_value=process):
            with self.assertRaises(CodexRunnerError) as raised:
                list(CodexRunner(self.settings).stream_new("hello"))

        message = str(raised.exception)
        self.assertIn("Model metadata for `gpt5.6` not found.", message)
        self.assertIn("The gpt5.6 model is not supported", message)
        self.assertIn("codex_json_errors:", message)


class _FakeProcess:
    def __init__(self, *, stdout: str, stderr: str, returncode: int) -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self._returncode = returncode

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode

    def kill(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()
