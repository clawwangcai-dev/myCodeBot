from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

from bridge_runner import RunnerError, RunnerResponse
from config import Settings


class ClaudeRunnerError(RunnerError):
    pass


class ClaudeRunner:
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
        )

    def _build_command(
        self,
        *,
        prompt: str,
        resume_session_id: str | None,
        output_format: str,
        permission_mode_override: str | None = None,
        include_partial_messages: bool = False,
    ) -> list[str]:
        command = [
            self._settings.claude_bin,
            '-p',
            prompt,
            '--output-format',
            output_format,
        ]

        if resume_session_id:
            command.extend(['--resume', resume_session_id])

        if self._settings.claude_settings_file:
            command.extend(['--settings', str(self._settings.claude_settings_file)])

        if include_partial_messages:
            command.append('--include-partial-messages')

        if output_format == 'stream-json':
            command.append('--verbose')

        permission_mode = permission_mode_override or self._settings.claude_permission_mode
        if permission_mode:
            command.extend(['--permission-mode', permission_mode])

        if self._settings.claude_allowed_tools:
            command.append('--allowedTools')
            command.extend(self._settings.claude_allowed_tools)

        if self._settings.claude_disallowed_tools:
            command.append('--disallowedTools')
            command.extend(self._settings.claude_disallowed_tools)

        return command

    def _run(
        self,
        prompt: str,
        resume_session_id: str | None,
        permission_mode_override: str | None = None,
    ) -> RunnerResponse:
        command = self._build_command(
            prompt=prompt,
            resume_session_id=resume_session_id,
            output_format=self._settings.claude_output_format,
            permission_mode_override=permission_mode_override,
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
            raise ClaudeRunnerError(
                f"claude timed out after {self._settings.claude_timeout_seconds}s\n"
                f"stdout:\n{(exc.stdout or '').strip() or '<empty>'}\n"
                f"stderr:\n{(exc.stderr or '').strip() or '<empty>'}"
            ) from exc

        if completed.returncode != 0:
            raise ClaudeRunnerError(
                f"claude exited with code {completed.returncode}\n"
                f"stderr:\n{completed.stderr.strip() or '<empty>'}\n"
                f"stdout:\n{completed.stdout.strip() or '<empty>'}"
            )

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeRunnerError(
                f"Failed to parse Claude JSON output: {exc}\nRaw stdout:\n{completed.stdout}"
            ) from exc

        session_id = self._extract_session_id(payload)
        text = self._extract_final_result(payload, allow_plain_text=True) or ''

        if not session_id:
            raise ClaudeRunnerError(f'Claude response did not include a session id: {payload}')

        return RunnerResponse(
            session_id=session_id,
            text=text.strip(),
            raw=payload,
            command=command,
        )

    def _stream_run(
        self,
        prompt: str,
        resume_session_id: str | None,
        permission_mode_override: str | None = None,
    ):
        command = self._build_command(
            prompt=prompt,
            resume_session_id=resume_session_id,
            output_format='stream-json',
            permission_mode_override=permission_mode_override,
            include_partial_messages=True,
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
            raise ClaudeRunnerError('claude stream failed to open stdout/stderr pipes')

        line_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        stderr_lines: list[str] = []

        def enqueue_lines(source: str, handle) -> None:
            for line in iter(handle.readline, ''):
                line_queue.put((source, line))
            handle.close()

        stdout_thread = threading.Thread(
            target=enqueue_lines,
            args=('stdout', process.stdout),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=enqueue_lines,
            args=('stderr', process.stderr),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        started_at = time.monotonic()
        session_id = resume_session_id
        current_text = ''
        saw_final = False

        while True:
            elapsed = time.monotonic() - started_at
            remaining = self._settings.claude_timeout_seconds - elapsed
            if remaining <= 0:
                process.kill()
                process.wait(timeout=5)
                raise ClaudeRunnerError(
                    f'claude timed out after {self._settings.claude_timeout_seconds}s\n'
                    f'stderr:\n{self._join_lines(stderr_lines)}'
                )

            try:
                source, line = line_queue.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if process.poll() is not None and line_queue.empty():
                    break
                continue

            if source == 'stderr':
                stderr_lines.append(line.rstrip())
                continue

            raw_line = line.strip()
            if not raw_line:
                continue

            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                stderr_lines.append(f'<non-json-stdout> {raw_line}')
                continue

            session_id = self._extract_session_id(payload) or session_id

            partial = self._extract_stream_partial(payload)
            if partial is not None:
                mode, text = partial
                if mode == 'append':
                    current_text += text
                else:
                    current_text = text
                if current_text:
                    yield {
                        'session_id': session_id,
                        'text': current_text,
                        'raw': payload,
                        'is_final': False,
                    }

            final_text = self._extract_final_result(payload)
            if final_text is not None:
                current_text = final_text.strip() or current_text
                saw_final = True
                yield {
                    'session_id': session_id,
                    'text': current_text,
                    'raw': payload,
                    'is_final': True,
                }

        returncode = process.wait(timeout=5)
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        if returncode != 0:
            raise ClaudeRunnerError(
                f'claude exited with code {returncode}\n'
                f'stderr:\n{self._join_lines(stderr_lines)}'
            )

        if not saw_final:
            if current_text:
                yield {
                    'session_id': session_id,
                    'text': current_text,
                    'raw': {'type': 'synthetic_final'},
                    'is_final': True,
                }
            else:
                raise ClaudeRunnerError(
                    'claude stream ended without a final result\n'
                    f'stderr:\n{self._join_lines(stderr_lines)}'
                )

    @staticmethod
    def _join_lines(lines: list[str]) -> str:
        joined = '\n'.join(line for line in lines if line)
        return joined or '<empty>'

    @staticmethod
    def _extract_session_id(payload: dict) -> str | None:
        return (
            payload.get('session_id')
            or payload.get('sessionId')
            or payload.get('session', {}).get('id')
        )

    @staticmethod
    def _extract_final_result(payload: dict, allow_plain_text: bool = False) -> str | None:
        result = payload.get('result')
        if isinstance(result, str):
            return result

        if allow_plain_text or payload.get('type') == 'result':
            for key in ('output', 'text'):
                value = payload.get(key)
                if isinstance(value, str):
                    return value

        return None

    @classmethod
    def _extract_stream_partial(cls, payload: dict) -> tuple[str, str] | None:
        partial_message = payload.get('partial_message')
        if isinstance(partial_message, dict):
            snapshot = cls._extract_message_text(partial_message)
            if snapshot is not None:
                return ('replace', snapshot)

        event_type = payload.get('type')
        if event_type == 'content_block_delta':
            delta = payload.get('delta') or {}
            text = delta.get('text')
            if isinstance(text, str):
                return ('append', text)

        for key in ('message', 'assistant_message'):
            value = payload.get(key)
            if isinstance(value, dict):
                snapshot = cls._extract_message_text(value)
                if snapshot is not None:
                    return ('replace', snapshot)

        if event_type in {'assistant', 'assistant_message', 'message', 'partial_message'}:
            snapshot = cls._extract_message_text(payload)
            if snapshot is not None:
                return ('replace', snapshot)

        text = payload.get('text')
        if isinstance(text, str) and event_type not in {'result'}:
            return ('replace', text)

        return None

    @staticmethod
    def _extract_message_text(payload: dict) -> str | None:
        content = payload.get('content')
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                    continue
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'text' and isinstance(block.get('text'), str):
                    parts.append(block['text'])
                    continue
                if isinstance(block.get('text'), str):
                    parts.append(block['text'])
            if parts:
                return ''.join(parts)

        text = payload.get('text')
        if isinstance(text, str):
            return text

        return None


def format_text_reply(text: str, limit: int = 4000) -> list[str]:
    clean = text.strip() or '(empty response)'
    if len(clean) <= limit:
        return [clean]

    parts: list[str] = []
    cursor = 0
    while cursor < len(clean):
        chunk = clean[cursor : cursor + limit]
        if len(chunk) == limit:
            split_at = chunk.rfind('\n')
            if split_at < 1000:
                split_at = chunk.rfind(' ')
            if split_at > 0:
                chunk = chunk[:split_at]
        chunk = chunk.rstrip()
        if not chunk:
            chunk = clean[cursor : cursor + limit]
        parts.append(chunk)
        cursor += len(chunk)
        while cursor < len(clean) and clean[cursor].isspace():
            cursor += 1
    return parts
