from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bridge_runner import RunnerError, RunnerResponse
from config import Settings


class CodexRunnerError(RunnerError):
    pass


class CodexRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def ask_new(
        self,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ) -> RunnerResponse:
        return self._run(
            prompt=prompt,
            resume_session_id=None,
            permission_mode_override=permission_mode_override,
            image_paths=image_paths or [],
        )

    def ask_resume(
        self,
        session_id: str,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ) -> RunnerResponse:
        return self._run(
            prompt=prompt,
            resume_session_id=session_id,
            permission_mode_override=permission_mode_override,
            image_paths=image_paths or [],
        )

    def stream_new(
        self,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ):
        yield from self._stream_run(
            prompt=prompt,
            resume_session_id=None,
            permission_mode_override=permission_mode_override,
            image_paths=image_paths or [],
        )

    def stream_resume(
        self,
        session_id: str,
        prompt: str,
        *,
        permission_mode_override: str | None = None,
        image_paths: list[str] | None = None,
    ):
        yield from self._stream_run(
            prompt=prompt,
            resume_session_id=session_id,
            permission_mode_override=permission_mode_override,
            image_paths=image_paths or [],
        )

    def _build_command(
        self,
        *,
        prompt: str,
        resume_session_id: str | None,
        permission_mode_override: str | None,
        image_paths: list[str],
    ) -> list[str]:
        if resume_session_id:
            command = [
                self._settings.codex_bin,
                "exec",
                "resume",
                "--json",
            ]
        else:
            command = [
                self._settings.codex_bin,
                "exec",
                "--json",
            ]

        if not resume_session_id:
            command.extend(["-C", str(self._settings.claude_workdir)])

        if self._settings.codex_model:
            command.extend(["-m", self._settings.codex_model])

        for image_path in image_paths:
            command.extend(["-i", image_path])

        if self._should_bypass(permission_mode_override):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.append("--full-auto")

        if resume_session_id:
            command.extend([resume_session_id, prompt])
        else:
            command.append(prompt)

        return command

    def _should_bypass(self, permission_mode_override: str | None) -> bool:
        approval_mode = permission_mode_override or self._settings.claude_permission_mode or ""
        if approval_mode == "bypassPermissions":
            return True

        return (
            self._settings.codex_sandbox == "danger-full-access"
            or self._settings.codex_approval_policy == "never"
        )

    def _run(
        self,
        *,
        prompt: str,
        resume_session_id: str | None,
        permission_mode_override: str | None,
        image_paths: list[str],
    ) -> RunnerResponse:
        command = self._build_command(
            prompt=prompt,
            resume_session_id=resume_session_id,
            permission_mode_override=permission_mode_override,
            image_paths=image_paths,
        )
        try:
            completed = subprocess.run(
                command,
                cwd=str(self._settings.claude_workdir),
                text=True,
                capture_output=True,
                check=False,
                timeout=self._settings.claude_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexRunnerError(
                f"codex timed out after {self._settings.claude_timeout_seconds}s\n"
                f"stdout:\n{(exc.stdout or '').strip() or '<empty>'}\n"
                f"stderr:\n{(exc.stderr or '').strip() or '<empty>'}"
            ) from exc

        if completed.returncode != 0:
            raise CodexRunnerError(
                f"codex exited with code {completed.returncode}\n"
                f"stderr:\n{completed.stderr.strip() or '<empty>'}\n"
                f"stdout:\n{completed.stdout.strip() or '<empty>'}"
            )

        events = self._parse_jsonl(completed.stdout)
        session_id = self._extract_session_id(events) or resume_session_id or ""
        text = self._extract_final_text(events)
        if not session_id:
            raise CodexRunnerError(f"Codex response did not include a session id: {events}")
        return RunnerResponse(session_id=session_id, text=text, raw={"events": events}, command=command)

    def _stream_run(
        self,
        *,
        prompt: str,
        resume_session_id: str | None,
        permission_mode_override: str | None,
        image_paths: list[str],
    ):
        command = self._build_command(
            prompt=prompt,
            resume_session_id=resume_session_id,
            permission_mode_override=permission_mode_override,
            image_paths=image_paths,
        )
        process = subprocess.Popen(
            command,
            cwd=str(self._settings.claude_workdir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise CodexRunnerError("codex stream failed to open stdout/stderr pipes")

        session_id = resume_session_id
        final_text = ""
        saw_final = False
        stderr_lines: list[str] = []
        for line in process.stdout:
            raw_line = line.strip()
            if not raw_line:
                continue
            payload = json.loads(raw_line)
            session_id = payload.get("thread_id") or session_id
            text = self._extract_event_text(payload)
            if text is not None:
                final_text = text
                if payload.get("type") == "item.completed":
                    saw_final = True
                yield {
                    "session_id": session_id,
                    "text": final_text,
                    "raw": payload,
                    "is_final": payload.get("type") == "item.completed",
                }

        stderr_lines.extend(line.rstrip() for line in process.stderr)
        returncode = process.wait(timeout=5)
        if returncode != 0:
            raise CodexRunnerError(
                f"codex exited with code {returncode}\n"
                f"stderr:\n{self._join_lines(stderr_lines)}"
            )
        if final_text and not saw_final:
            yield {
                "session_id": session_id,
                "text": final_text,
                "raw": {"type": "synthetic_final"},
                "is_final": True,
            }

    @staticmethod
    def _parse_jsonl(stdout: str) -> list[dict]:
        events: list[dict] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    @staticmethod
    def _extract_session_id(events: list[dict]) -> str | None:
        for event in events:
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
        return None

    @classmethod
    def _extract_final_text(cls, events: list[dict]) -> str:
        text = ""
        for event in events:
            value = cls._extract_event_text(event)
            if value is not None:
                text = value
        return text.strip()

    @staticmethod
    def _extract_event_text(event: dict) -> str | None:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                return text
        return None

    @staticmethod
    def _join_lines(lines: list[str]) -> str:
        joined = "\n".join(line for line in lines if line)
        return joined or "<empty>"
