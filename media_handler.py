from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import Settings


class MediaHandlerError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    mime_type: str | None
    file_id: str
    caption: str


@dataclass(frozen=True)
class VoiceTranscript:
    media: DownloadedMedia
    text: str


class MediaHandler:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._root = settings.media_store_path
        self._root.mkdir(parents=True, exist_ok=True)

    def build_image_prompt(self, media: DownloadedMedia) -> str:
        caption_block = media.caption.strip() or "(no caption)"
        return (
            "The user sent an image.\n"
            f"Image path: {media.path}\n"
            f"MIME type: {media.mime_type or 'unknown'}\n"
            f"Caption: {caption_block}\n\n"
            "Please inspect the image file from the local path above and answer the user's request. "
            "If the caption includes instructions, follow them."
        )

    def build_voice_prompt(self, transcript: VoiceTranscript) -> str:
        return (
            "The user sent a voice message.\n"
            f"Audio path: {transcript.media.path}\n"
            f"Transcription:\n{transcript.text.strip() or '(empty transcription)'}\n\n"
            "Please respond to the user based on the transcription above."
        )

    def download(
        self,
        *,
        file_url: str,
        file_id: str,
        file_name: str,
        mime_type: str | None,
        caption: str,
        headers: dict[str, str] | None = None,
    ) -> DownloadedMedia:
        target_dir = self._root
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / file_name
        try:
            request = Request(file_url, method="GET")
            for key, value in (headers or {}).items():
                request.add_header(key, value)
            with urlopen(request, timeout=60) as response, target_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        except HTTPError as exc:
            raise MediaHandlerError(f"Download failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise MediaHandlerError(f"Download failed: {exc}") from exc

        return DownloadedMedia(
            path=target_path,
            mime_type=mime_type,
            file_id=file_id,
            caption=caption,
        )

    def transcribe_voice(self, media: DownloadedMedia) -> VoiceTranscript:
        models = self._transcription_models()
        failures: list[str] = []

        transcript = self._transcribe_with_faster_whisper(media, models, failures)
        if transcript is not None:
            return transcript

        transcript = self._transcribe_with_whisper_cli(media, models, failures)
        if transcript is not None:
            return transcript

        raise MediaHandlerError(
            "语音转写失败。已按顺序尝试 faster-whisper 和 whisper CLI。"
            f"\n当前配置 WHISPER_BIN={self._settings.whisper_bin}"
            f"\n已尝试模型: {', '.join(models) or '<none>'}\n"
            + "\n".join(failures)
        )

    def _transcription_models(self) -> list[str]:
        seen: set[str] = set()
        models: list[str] = []
        for model_name in [self._settings.whisper_model, *self._settings.whisper_fallback_models]:
            model = model_name.strip()
            if not model or model in seen:
                continue
            seen.add(model)
            models.append(model)
        return models

    def _transcribe_with_faster_whisper(
        self,
        media: DownloadedMedia,
        models: list[str],
        failures: list[str],
    ) -> VoiceTranscript | None:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            failures.append(
                f"faster-whisper unavailable: {exc.__class__.__name__}: {exc}"
            )
            return None

        for model_name in models:
            try:
                model = WhisperModel(
                    model_name,
                    device="auto",
                    cpu_threads=self._settings.whisper_threads,
                )
                segments, _info = model.transcribe(
                    str(media.path),
                    task="transcribe",
                    language=self._settings.whisper_language,
                )
                text = "".join(segment.text for segment in segments).strip()
                return VoiceTranscript(media=media, text=text)
            except Exception as exc:
                failures.append(
                    f"faster-whisper {model_name}: {exc.__class__.__name__}: {exc}"
                )

        return None

    def _transcribe_with_whisper_cli(
        self,
        media: DownloadedMedia,
        models: list[str],
        failures: list[str],
    ) -> VoiceTranscript | None:
        whisper_path = shutil.which(self._settings.whisper_bin)
        if whisper_path is None:
            failures.append(
                "whisper CLI unavailable: "
                f"WHISPER_BIN={self._settings.whisper_bin}, resolved=missing"
            )
            return None

        for model in models:
            output_dir = media.path.parent / f"{media.path.stem}-whisper-{model}"
            output_dir.mkdir(parents=True, exist_ok=True)

            command = [
                whisper_path,
                str(media.path),
                "--model",
                model,
                "--output_dir",
                str(output_dir),
                "--output_format",
                "json",
                "--verbose",
                "False",
                "--task",
                "transcribe",
                "--fp16",
                "False",
                "--threads",
                str(self._settings.whisper_threads),
            ]
            if self._settings.whisper_language:
                command.extend(["--language", self._settings.whisper_language])

            completed = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=self._settings.claude_timeout_seconds,
            )
            if completed.returncode == 0:
                transcript_path = output_dir / f"{media.path.stem}.json"
                if not transcript_path.exists():
                    failures.append(f"{model}: missing transcript file")
                    continue

                payload = json.loads(transcript_path.read_text(encoding="utf-8"))
                text = payload.get("text")
                if not isinstance(text, str):
                    failures.append(f"{model}: unexpected output")
                    continue
                return VoiceTranscript(media=media, text=text.strip())

            detail = (
                f"whisper-cli {model}: exit {completed.returncode}, "
                f"stderr={completed.stderr.strip() or '<empty>'}, "
                f"stdout={completed.stdout.strip() or '<empty>'}"
            )
            failures.append(detail)
            if completed.returncode == -9:
                continue

        return None
