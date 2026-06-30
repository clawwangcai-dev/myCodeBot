# Repository Guidelines

## Project Structure & Module Organization

This repository implements a local agent bridge for Telegram, WhatsApp, and a status web UI. Core Python modules live at the repository root: `bot.py` is the Telegram entrypoint, `bridge_core.py` holds shared channel behavior, `*_runner.py` files wrap Claude/Codex/Copilot CLIs, and stores/schedulers are split into modules such as `session_store.py`, `workdir_store.py`, and `reminder_store.py`. Runtime examples and service files are in `systemd/`. OpenSpec proposals and task plans are in `openspec/`. Generated runtime data such as media downloads, caches, and local env files should stay out of git.

## Build, Test, and Development Commands

- `python3 bot.py`: run the bridge in the foreground using local environment configuration.
- `python3 install_service.py install`: install the user-level systemd service.
- `python3 install_service.py restart`: restart the installed service after changes.
- `python3 install_service.py status`: inspect service state.
- `python3 -m py_compile *.py`: quick syntax check for all root Python modules.

`requirements.txt` currently declares no third-party Python packages; the bridge depends on local CLIs such as `claude`, `codex`, `copilot`/`gh`, and optional `whisper`.

## Coding Style & Naming Conventions

Use Python 3 with explicit imports, type hints, and small modules grouped by responsibility. Follow the existing style: 4-space indentation, `snake_case` functions and variables, `PascalCase` classes, uppercase constants, and `from __future__ import annotations` in Python files. Prefer `pathlib.Path` for filesystem work and dataclasses for structured settings or state. Keep user-facing text centralized where existing modules already do so.

## Testing Guidelines

There is no committed automated test suite yet. For changes, at minimum run `python3 -m py_compile *.py`. When touching bridge behavior, manually exercise relevant commands through a configured bot or local web UI, such as `/status`, `/project_status`, `/resume_local`, and reminder commands. If adding tests, use `test_*.py` naming and keep fixtures isolated from real Telegram, WhatsApp, and CLI credentials.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Improve transcription fallback and UTF-8 handling` and `Add managed scheduled reminders`. Keep the first line concise and focused on one change. Pull requests should describe behavior changes, list manual or automated verification, call out configuration or service changes, and include screenshots or chat transcripts when UI/channel behavior changes.

## Security & Configuration Tips

Never commit bot tokens, WhatsApp access tokens, Claude settings, or local session stores. Use `systemd/telegram-claude-bridge.env.example` as the template and keep real env files under `~/.config/telegram-claude-bridge/`. Preserve workspace restrictions around `CLAUDE_WORKDIR` and `CLAUDE_ALLOWED_WORKDIRS` when modifying project switching logic.
