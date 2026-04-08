from __future__ import annotations

import json
import subprocess

from bridge_runner import RunnerError, RunnerResponse
from config import Settings


class CopilotRunnerError(RunnerError):
    pass


class CopilotRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def ask_new(
        self,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ) -> RunnerResponse:
        return self._run(prompt=prompt, resume_session_id=None)

    def ask_resume(
        self,
        session_id: str,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ) -> RunnerResponse:
        return self._run(prompt=prompt, resume_session_id=session_id)

    def stream_new(
        self,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ):
        yield from self._stream_run(prompt=prompt, resume_session_id=None)

    def stream_resume(
        self,
        session_id: str,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ):
        yield from self._stream_run(prompt=prompt, resume_session_id=session_id)

    def _base_command(self) -> list[str]:
        if self._settings.copilot_use_gh:
            return ["gh", "copilot", "--"]
        return [self._settings.copilot_bin]

    def _build_command(self, *, prompt: str, resume_session_id: str | None, streaming: bool) -> list[str]:
        command = [
            *self._base_command(),
            "-p",
            prompt,
            "--silent",
            "--output-format",
            "json",
            "--stream",
            "on" if streaming else "off",
            "--allow-all",
            "--no-ask-user",
        ]
        if resume_session_id:
            command.extend(["--resume", resume_session_id])
        if self._settings.copilot_model:
            command.extend(["--model", self._settings.copilot_model])
        return command

    def _run(self, *, prompt: str, resume_session_id: str | None) -> RunnerResponse:
        command = self._build_command(
            prompt=prompt,
            resume_session_id=resume_session_id,
            streaming=False,
        )
        try:
            completed = subprocess.run(
                command,
                cwd=str(self._settings.claude_workdir),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=self._settings.claude_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise CopilotRunnerError(
                f"copilot timed out after {self._settings.claude_timeout_seconds}s\n"
                f"stdout:\n{(exc.stdout or '').strip() or '<empty>'}\n"
                f"stderr:\n{(exc.stderr or '').strip() or '<empty>'}"
            ) from exc

        events = self._parse_jsonl(completed.stdout)
        session_id = self._extract_session_id(events) or resume_session_id or ""
        text = self._extract_final_text(events)
        if completed.returncode != 0:
            raise CopilotRunnerError(
                f"copilot exited with code {completed.returncode}\n"
                f"stderr:\n{completed.stderr.strip() or '<empty>'}\n"
                f"stdout:\n{completed.stdout.strip() or '<empty>'}"
            )
        if not session_id:
            raise CopilotRunnerError(f"Copilot response did not include a session id: {events}")
        return RunnerResponse(session_id=session_id, text=text, raw={"events": events}, command=command)

    def _stream_run(self, *, prompt: str, resume_session_id: str | None):
        command = self._build_command(
            prompt=prompt,
            resume_session_id=resume_session_id,
            streaming=True,
        )
        process = subprocess.Popen(
            command,
            cwd=str(self._settings.claude_workdir),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise CopilotRunnerError("copilot stream failed to open stdout/stderr pipes")

        session_id = resume_session_id
        current_text = ""
        message_texts: dict[str, str] = {}
        saw_final = False

        for line in process.stdout:
            raw_line = line.strip()
            if not raw_line:
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            session_id = self._extract_session_id([payload]) or session_id
            next_text = self._extract_event_text(payload, message_texts)
            if next_text is not None:
                current_text = next_text
                yield {
                    "session_id": session_id,
                    "text": current_text,
                    "raw": payload,
                    "is_final": False,
                }
            if payload.get("type") == "assistant.turn_end" and current_text:
                saw_final = True
                yield {
                    "session_id": session_id,
                    "text": current_text,
                    "raw": payload,
                    "is_final": True,
                }

        stderr_lines = [line.rstrip() for line in process.stderr]
        returncode = process.wait(timeout=5)
        if returncode != 0:
            raise CopilotRunnerError(
                f"copilot exited with code {returncode}\n"
                f"stderr:\n{self._join_lines(stderr_lines)}"
            )
        if current_text and not saw_final:
            yield {
                "session_id": session_id,
                "text": current_text,
                "raw": {"type": "synthetic_final"},
                "is_final": True,
            }

    @staticmethod
    def _parse_jsonl(stdout: str) -> list[dict]:
        events: list[dict] = []
        for line in stdout.splitlines():
            raw_line = line.strip()
            if not raw_line:
                continue
            try:
                events.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
        return events

    @staticmethod
    def _extract_session_id(events: list[dict]) -> str | None:
        for event in events:
            data = event.get("data")
            if isinstance(data, dict):
                session_id = data.get("sessionId")
                if isinstance(session_id, str) and session_id:
                    return session_id
        return None

    @classmethod
    def _extract_final_text(cls, events: list[dict]) -> str:
        text = ""
        message_texts: dict[str, str] = {}
        for event in events:
            value = cls._extract_event_text(event, message_texts)
            if value is not None:
                text = value
        return text.strip()

    @staticmethod
    def _extract_event_text(event: dict, message_texts: dict[str, str]) -> str | None:
        event_type = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            return None

        if event_type == "assistant.message":
            message_id = data.get("messageId")
            content = data.get("content")
            if isinstance(message_id, str) and isinstance(content, str):
                message_texts[message_id] = content
                return content
            if isinstance(content, str):
                return content

        if event_type == "assistant.message_delta":
            message_id = data.get("messageId")
            delta = data.get("deltaContent")
            if isinstance(message_id, str) and isinstance(delta, str):
                message_texts[message_id] = message_texts.get(message_id, "") + delta
                return message_texts[message_id]

        return None

    @staticmethod
    def _join_lines(lines: list[str]) -> str:
        joined = "\n".join(line for line in lines if line)
        return joined or "<empty>"
