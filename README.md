# Telegram Claude CLI Bridge | Telegram Claude CLI 桥接服务

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

Minimal bridge that forwards Telegram text messages to the local `claude` CLI and keeps one Claude session per Telegram chat.

### Environment

Set these variables before starting:

```bash
export TELEGRAM_BOT_TOKEN=...
export CLAUDE_BIN=claude
export CLAUDE_WORKDIR=/home/chao/projects/safe-repo
export CLAUDE_SETTINGS_FILE=/home/chao/.config/telegram-claude-bridge/claude-settings.json
export CLAUDE_PERMISSION_MODE=default
export CLAUDE_APPROVAL_PERMISSION_MODE=acceptEdits
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
export STATUS_WEB_ENABLED=true
export STATUS_WEB_HOST=127.0.0.1
export STATUS_WEB_PORT=8765
export STATUS_WEB_TOKEN=
export APPROVAL_STORE_PATH=approval_prefs.json
export MEDIA_STORE_PATH=.telegram-media
export WHISPER_BIN=whisper
export WHISPER_MODEL=base
export WHISPER_FALLBACK_MODELS=tiny
export WHISPER_LANGUAGE=
export WHISPER_THREADS=2
```

### Run

```bash
python3 bot.py
```

Or install a background service with automatic platform detection:

```bash
python3 install_service.py
```

### Commands

- `/start` - Start session
- `/status` - View status
- `/health` - Health check
- `/version` - View version
- `/clear` - Clear session
- `/approve` - Approve pending permission request
- `/approve-always` - Always auto-approve future edit/write requests in this chat
- `/approve_bypass` - Always auto-approve broader permissions including Bash/git in this chat
- `/approve-manual` - Turn off auto-approve for this chat
- `/deny` - Deny pending permission request

### Notes

- Each Telegram chat is serialized with a per-chat lock to avoid concurrent writes into the same Claude session.
- The default path uses `claude -p ... --output-format json` for both new and resumed conversations.
- Set `CLAUDE_STREAMING=true` to switch to `--output-format stream-json --include-partial-messages` and stream partial replies by editing the in-flight Telegram message.
- Keep the bridge default at `CLAUDE_PERMISSION_MODE=default`; when Claude replies that it needs write/edit permission, the bridge records a one-shot pending approval and you can continue from Telegram with `/approve` or cancel with `/deny`.
- `/approve` retries the blocked task in the same chat using `CLAUDE_APPROVAL_PERMISSION_MODE` (defaults to `acceptEdits`) instead of permanently loosening permissions for all requests.
- `/approve-always` stores a per-chat preference and will keep auto-continuing future edit/write permission requests in that chat until you disable it with `/approve-manual`.
- `/approve_bypass` stores a per-chat preference using `bypassPermissions`. This is broader and is intended for cases like `git add`, `git commit`, or other Bash-level actions. Use it carefully.
- The bridge now accepts Telegram image messages and voice/audio messages. Images are downloaded into the workspace media directory and passed to Claude as local files; voice messages are transcribed with the local `whisper` CLI before forwarding to Claude.
- The bridge does not auto-install Whisper. It first uses the system `whisper` if found; otherwise set `WHISPER_BIN` to the actual executable path.
- Voice transcription defaults to a conservative `base` model and falls back to `tiny` to reduce OOM risk on smaller machines.
- The bridge can load a service-specific Claude settings override with `CLAUDE_SETTINGS_FILE`; the bundled example disables `semgrep` plus the explanatory/learning output-style plugins only for this Telegram service.
- On this machine, the `systemd` unit also needs `node` on `PATH` because a Claude SessionEnd hook invokes Node.js.
- A local-only status page is enabled by default at `http://127.0.0.1:8765/` with JSON at `http://127.0.0.1:8765/api/status`.
- If you set `STATUS_WEB_TOKEN`, the page requires either `Authorization: Bearer <token>` or `?token=<token>`.
- Keep `CLAUDE_WORKDIR` narrow and set tool permissions conservatively before exposing this bot to real users.

### Service Install

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

#### Linux Manual Install

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

---

<a name="中文"></a>
## 中文

轻量级桥接服务，将 Telegram 文本消息转发到本地 `claude` CLI，并为每个 Telegram 聊天保持一个独立的 Claude 会话。

### 环境变量

启动前设置以下变量：

```bash
export TELEGRAM_BOT_TOKEN=...
export CLAUDE_BIN=claude
export CLAUDE_WORKDIR=/home/chao/projects/safe-repo
export CLAUDE_SETTINGS_FILE=/home/chao/.config/telegram-claude-bridge/claude-settings.json
export CLAUDE_PERMISSION_MODE=default
export CLAUDE_APPROVAL_PERMISSION_MODE=acceptEdits
export CLAUDE_ALLOWED_TOOLS=
export CLAUDE_DISALLOWED_TOOLS=Bash(rm),Bash(git reset)
export CLAUDE_TIMEOUT_SECONDS=300
export CLAUDE_STREAMING=true
```

可选变量：

```bash
export SESSION_STORE_PATH=sessions.json
export TELEGRAM_POLL_TIMEOUT=30
export TELEGRAM_EDIT_INTERVAL_SECONDS=1.0
export TELEGRAM_API_BASE=https://api.telegram.org
export STATUS_WEB_ENABLED=true
export STATUS_WEB_HOST=127.0.0.1
export STATUS_WEB_PORT=8765
export STATUS_WEB_TOKEN=
export APPROVAL_STORE_PATH=approval_prefs.json
export MEDIA_STORE_PATH=.telegram-media
export WHISPER_BIN=whisper
export WHISPER_MODEL=base
export WHISPER_FALLBACK_MODELS=tiny
export WHISPER_LANGUAGE=
export WHISPER_THREADS=2
```

### 运行

```bash
python3 bot.py
```

或安装为后台服务（自动检测平台）：

```bash
python3 install_service.py
```

### 命令

- `/start` - 开始会话
- `/status` - 查看状态
- `/health` - 健康检查
- `/version` - 查看版本
- `/clear` - 清除会话
- `/approve` - 批准待处理的权限请求
- `/approve-always` - 当前 chat 后续自动批准编辑/写入权限请求
- `/approve_bypass` - 当前 chat 后续自动批准更高权限请求，包括 Bash/git
- `/approve-manual` - 关闭当前 chat 的自动批准
- `/deny` - 拒绝待处理的权限请求

### 注意事项

- 每个 Telegram 聊天使用独立锁进行序列化，避免对同一 Claude 会话的并发写入。
- 默认使用 `claude -p ... --output-format json` 处理新会话和恢复的对话。
- 设置 `CLAUDE_STREAMING=true` 可切换到 `--output-format stream-json --include-partial-messages`，通过编辑正在发送的消息实现流式回复。
- 保持桥接服务默认使用 `CLAUDE_PERMISSION_MODE=default`；当 Claude 回复需要写入/编辑权限时，桥接服务会记录一次性待批准请求，你可以通过 Telegram 使用 `/approve` 继续或使用 `/deny` 取消。
- `/approve` 使用 `CLAUDE_APPROVAL_PERMISSION_MODE`（默认为 `acceptEdits`）在同一聊天中重试被阻止的任务，而不是永久放宽所有请求的权限。
- `/approve-always` 会为当前 chat 保存一个持久化偏好；之后检测到编辑/写入权限请求时会持续自动续跑，直到你用 `/approve-manual` 显式关闭。
- `/approve_bypass` 会为当前 chat 保存一个更高权限的持久化偏好，适合 `git add`、`git commit` 或其他 Bash 级动作；它会使用 `bypassPermissions`，风险更高，开启时要明确知道自己在放开什么。
- 现在已支持 Telegram 图片和语音/音频消息：图片会下载到工作目录下的媒体目录，并作为本地文件路径交给 Claude；语音消息会先通过本机 `whisper` CLI 转写，再把转写文本发给 Claude。
- bridge 不会自动安装 Whisper；它会优先复用系统里已有的 `whisper`。如果未找到，请在环境变量里把 `WHISPER_BIN` 指到实际可执行文件路径。
- 为了降低小内存机器上的 OOM 风险，语音转写默认使用较保守的 `base` 模型，并在失败时自动降级到 `tiny`。
- 桥接服务可通过 `CLAUDE_SETTINGS_FILE` 加载服务专属的 Claude 设置覆盖文件；附带的示例仅为此 Telegram 服务禁用了 `semgrep` 及说明性/学习性输出风格插件。
- 在本机上，`systemd` 单元还需要 `node` 在 `PATH` 中，因为 Claude SessionEnd 钩子会调用 Node.js。
- 默认启用本地状态页面：`http://127.0.0.1:8765/`，JSON 接口：`http://127.0.0.1:8765/api/status`。
- 如果设置了 `STATUS_WEB_TOKEN`，访问页面需要 `Authorization: Bearer <token>` 或 `?token=<token>`。
- 在将此机器人暴露给真实用户之前，请将 `CLAUDE_WORKDIR` 限制在狭窄范围，并保守地设置工具权限。

### 服务安装

推荐使用自动安装器。它会检测 Linux、macOS 或 Windows 并安装对应的后台服务：

```bash
python3 install_service.py install
```

它会创建平台专属的环境变量文件、复制桥接服务专属的 Claude 设置覆盖文件、安装服务并启动（除非传入 `--no-start`）。

支持的平台：

- Linux: `systemd --user`
- macOS: `launchd` 用户代理
- Windows: 任务计划程序 (`schtasks`)

示例：

```bash
python3 install_service.py install
python3 install_service.py install --platform macos --no-start
python3 install_service.py install --platform windows
python3 install_service.py status
python3 install_service.py restart
python3 install_service.py uninstall
```

运行时路径通过 [service_entry.py](/home/chao/projects/claudeBot/service_entry.py) 在各平台统一管理，它会加载环境变量文件并为常见的 Claude/Node 安装路径补丁 `PATH`，然后启动机器人。

#### Linux 手动安装

如果你仍想使用手动 Linux 安装方式：

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

如果你希望 Linux 用户服务在重启后无需活跃登录会话也能运行：

```bash
loginctl enable-linger "$USER"
```
