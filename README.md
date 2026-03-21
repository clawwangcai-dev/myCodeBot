# Telegram Claude CLI Bridge

Minimal bridge that forwards Telegram text messages to the local `claude` CLI and keeps one Claude session per Telegram chat.

## Environment

Set these variables before starting:

```bash
export TELEGRAM_BOT_TOKEN=...
export CLAUDE_BIN=claude
export CLAUDE_WORKDIR=/home/chao/projects/safe-repo
export CLAUDE_SETTINGS_FILE=/home/chao/.config/telegram-claude-bridge/claude-settings.json
export CLAUDE_PERMISSION_MODE=default
export CLAUDE_ALLOWED_TOOLS=
export CLAUDE_DISALLOWED_TOOLS=Bash(rm),Bash(git reset)
export CLAUDE_TIMEOUT_SECONDS=300
export CLAUDE_STREAMING=true
```

Optional:

```bash
export SESSION_STORE_PATH=sessions.json
export TELEGRAM_POLL_TIMEOUT=30
export TELEGRAM_EDIT_INTERVAL_SECONDS=1.0
export TELEGRAM_API_BASE=https://api.telegram.org
```

## Run

```bash
python3 bot.py
```

Or install a background service with automatic platform detection:

```bash
python3 install_service.py
```

## Commands

- `/start`
- `/status`
- `/health`
- `/version`
- `/clear`

## Notes

- Each Telegram chat is serialized with a per-chat lock to avoid concurrent writes into the same Claude session.
- The default path uses `claude -p ... --output-format json` for both new and resumed conversations.
- Set `CLAUDE_STREAMING=true` to switch to `--output-format stream-json --include-partial-messages` and stream partial replies by editing the in-flight Telegram message.
- The bridge can load a service-specific Claude settings override with `CLAUDE_SETTINGS_FILE`; the bundled example disables `semgrep` plus the explanatory/learning output-style plugins only for this Telegram service.
- On this machine, the `systemd` unit also needs `node` on `PATH` because a Claude SessionEnd hook invokes Node.js.
- A local-only status page is enabled by default at `http://127.0.0.1:8765/` with JSON at `http://127.0.0.1:8765/api/status`.
- Keep `CLAUDE_WORKDIR` narrow and set tool permissions conservatively before exposing this bot to real users.

## Service Install

The preferred path is the auto-installer. It detects Linux, macOS, or Windows and installs the matching background service:

```bash
python3 install_service.py install
```

It creates a platform-specific env file, copies the bridge-specific Claude settings override, installs the service, and starts it unless you pass `--no-start`.

Platform targets:

- Linux: `systemd --user`
- macOS: `launchd` user agent
- Windows: Task Scheduler (`schtasks`)

Examples:

```bash
python3 install_service.py install
python3 install_service.py install --platform macos --no-start
python3 install_service.py install --platform windows
python3 install_service.py status
python3 install_service.py restart
python3 install_service.py uninstall
```

The runtime path is unified across platforms through [service_entry.py](/home/chao/projects/claudeBot/service_entry.py), which loads the env file and patches `PATH` for common Claude/Node installations before starting the bot.

### Linux Manual Install

If you still want the manual Linux path:

```bash
mkdir -p ~/.config/systemd/user ~/.config/telegram-claude-bridge
cp /home/chao/projects/claudeBot/systemd/telegram-claude-bridge.service ~/.config/systemd/user/
cp /home/chao/projects/claudeBot/systemd/telegram-claude-bridge.env.example ~/.config/telegram-claude-bridge/env
cp /home/chao/projects/claudeBot/systemd/telegram-claude-bridge.claude-settings.json ~/.config/telegram-claude-bridge/claude-settings.json
$EDITOR ~/.config/telegram-claude-bridge/env
systemctl --user daemon-reload
systemctl --user enable --now telegram-claude-bridge.service
journalctl --user -u telegram-claude-bridge.service -f
```

If you want the Linux user service to survive reboots without an active login session:

```bash
loginctl enable-linger "$USER"
```
