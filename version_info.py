from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from config import Settings


def _run_capture(command: list[str], cwd: Path | None = None) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or completed.stderr).strip()
    return value or None


def _package_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def get_version_snapshot(settings: Settings) -> dict[str, str]:
    repo_dir = Path(__file__).resolve().parent
    git_commit = _run_capture(["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir) or "unknown"
    claude_version = _run_capture([settings.claude_bin, "--version"]) or "unknown"
    codex_version = _run_capture([settings.codex_bin, "--version"]) or "unknown"
    faster_whisper_version = _package_version("faster-whisper") or "missing"
    whisper_resolved = shutil.which(settings.whisper_bin) or "missing"
    copilot_version = (
        _run_capture(["gh", "copilot", "--", "--version"])
        if settings.copilot_use_gh
        else _run_capture([settings.copilot_bin, "--version"])
    ) or "unknown"
    transcription_backend = (
        "faster-whisper"
        if faster_whisper_version != "missing"
        else "whisper-cli"
        if whisper_resolved != "missing"
        else "missing"
    )
    return {
        "app": "telegram-claude-bridge",
        "bridge_name": settings.name,
        "git_commit": git_commit,
        "provider": settings.provider,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "claude_bin": settings.claude_bin,
        "claude_version": claude_version,
        "codex_bin": settings.codex_bin,
        "codex_version": codex_version,
        "copilot_bin": "gh copilot" if settings.copilot_use_gh else settings.copilot_bin,
        "copilot_version": copilot_version,
        "transcription_backend": transcription_backend,
        "faster_whisper_version": faster_whisper_version,
        "whisper_bin": settings.whisper_bin,
        "whisper_resolved": whisper_resolved,
        "executable": sys.executable,
    }
