# Telegram Agent CLI Bridge | Telegram Agent CLI 桥接服务

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

This project turns Telegram into a remote entrypoint for a local agent CLI.

It currently supports:
- `claude`
- `codex`
- `copilot`
- single bot mode
- multi-bot mode
- text, image, and voice messages
- per-chat project directories

The tutorial below starts from zero and ends with multiple Telegram bots talking to local Claude Code, Codex, and GitHub Copilot CLI on the same machine.

### 1. What You Need

Before you start, make sure this machine already has:
- Python 3
- `claude` CLI
- `codex` CLI if you want Codex
- `copilot` CLI or `gh` if you want GitHub Copilot CLI
- `node` on `PATH` if your Claude hooks need it
- `whisper` on `PATH` if you want voice transcription
- at least one Telegram bot token from BotFather

Important:
- This bridge does not install Claude, Codex, or Whisper for you.
- Telegram is only the chat frontend. Real execution still happens locally on this machine.

### 2. Clone The Project

```bash
git clone https://github.com/clawwangcai-dev/myCodeBot.git ~/myCodeBot
cd ~/myCodeBot
```

### 3. Create The Runtime Config

Copy the example env file:

```bash
mkdir -p ~/.config/telegram-claude-bridge
cp ~/myCodeBot/systemd/telegram-claude-bridge.env.example ~/.config/telegram-claude-bridge/env
cp ~/myCodeBot/systemd/telegram-claude-bridge.claude-settings.json ~/.config/telegram-claude-bridge/claude-settings.json
```

Edit the env file:

```bash
$EDITOR ~/.config/telegram-claude-bridge/env
```

For a minimal single-bot setup, fill these first:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
BRIDGE_PROVIDER=claude
CLAUDE_WORKDIR=~/projects/safe-repo
CLAUDE_SETTINGS_FILE=~/.config/telegram-claude-bridge/claude-settings.json
CLAUDE_STREAMING=true
```

If you want Codex instead:

```bash
BRIDGE_PROVIDER=codex
CODEX_SANDBOX=danger-full-access
CODEX_APPROVAL_POLICY=never
```

If you want GitHub Copilot CLI instead:

```bash
BRIDGE_PROVIDER=copilot
COPILOT_USE_GH=true
```

Important safety rule:
- `CLAUDE_WORKDIR` is the default workspace root.
- Per-chat `/project` switching is allowed only inside this root.

### 4. Start The Bridge

Foreground:

```bash
python3 ~/myCodeBot/bot.py
```

Background service:

```bash
python3 ~/myCodeBot/install_service.py install
python3 ~/myCodeBot/install_service.py restart
python3 ~/myCodeBot/install_service.py status
```

Linux manual service:

```bash
mkdir -p ~/.config/systemd/user
cp ~/myCodeBot/systemd/telegram-claude-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now telegram-claude-bridge.service
journalctl --user -u telegram-claude-bridge.service -f
```

### 5. Test The First Bot In Telegram

Open the Telegram bot and send:

```text
/start
```

Then try:

```text
/status
```

Then send a normal prompt:

```text
who are you?
```

### 6. Core Telegram Commands

- `/start` show help
- `/help` show help
- `/status` show chat status
- `/health` show bridge runtime health
- `/version` show provider and binary versions
- `/clear` clear the current backend session
- `/project <path>` bind this chat to a project directory
- `/project_status` show the current chat project directory
- `/approve` approve one pending permission request
- `/approve_always` auto-approve future edit/write requests in this chat
- `/approve_bypass` auto-approve broader Bash/git-level requests in this chat
- `/approve_manual` turn auto-approve off
- `/deny` deny the pending request

### 7. Start A New Project From Telegram

If your default root is:

```bash
CLAUDE_WORKDIR=~/projects
```

Then in Telegram you can do:

```text
/project ~/projects/my-new-app
```

After that:

```text
Initialize a new FastAPI project here.
```

What `/project` does:
- creates the directory if needed
- binds the current Telegram chat to that directory
- clears the old backend session

What it does not allow:
- switching outside the configured default `CLAUDE_WORKDIR`
- using arbitrary paths like `/etc`

To return to the default root:

```text
/project default
```

### 8. Switching Between Claude, Codex, And Copilot

In single-bot mode, switching is global.

Use Claude:

```bash
BRIDGE_PROVIDER=claude
```

Use Codex:

```bash
BRIDGE_PROVIDER=codex
CODEX_SANDBOX=danger-full-access
CODEX_APPROVAL_POLICY=never
```

Use Copilot:

```bash
BRIDGE_PROVIDER=copilot
COPILOT_USE_GH=true
```

Then restart the service and in Telegram send:

```text
/clear
```

Note for this Linux machine:
- Codex `workspace-write` / `--full-auto` may hit `bwrap: Unknown option --argv0`
- the verified workaround here is:
  - `CODEX_SANDBOX=danger-full-access`
  - `CODEX_APPROVAL_POLICY=never`

### 9. Images And Voice

Supported now:
- text messages
- image messages
- voice/audio messages

Image flow:
- Telegram file downloads locally
- the local image path is passed to the selected backend

Voice flow:
- Telegram file downloads locally
- local `whisper` transcribes it
- the transcript is forwarded to the backend

If voice fails, check:
- `WHISPER_BIN`
- memory usage
- model size

Recommended conservative defaults:

```bash
WHISPER_MODEL=base
WHISPER_FALLBACK_MODELS=tiny
WHISPER_THREADS=2
```

### 10. Single-Bot Recommended Env

This is a solid starting point:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token
BOTS_CONFIG_FILE=
BRIDGE_PROVIDER=codex
CLAUDE_BIN=claude
CLAUDE_WORKDIR=~/projects
CLAUDE_SETTINGS_FILE=~/.config/telegram-claude-bridge/claude-settings.json
CLAUDE_PERMISSION_MODE=default
CLAUDE_APPROVAL_PERMISSION_MODE=acceptEdits
CLAUDE_TIMEOUT_SECONDS=300
CLAUDE_STREAMING=true
TELEGRAM_POLL_TIMEOUT=30
TELEGRAM_EDIT_INTERVAL_SECONDS=1.0
SESSION_STORE_PATH=~/myCodeBot/sessions.json
WORKDIR_STORE_PATH=~/myCodeBot/chat_workdirs.json
APPROVAL_STORE_PATH=~/myCodeBot/approval_prefs.json
MEDIA_STORE_PATH=~/myCodeBot/.telegram-media
WHISPER_BIN=whisper
WHISPER_MODEL=base
WHISPER_FALLBACK_MODELS=tiny
WHISPER_THREADS=2
CODEX_BIN=codex
CODEX_SANDBOX=danger-full-access
CODEX_APPROVAL_POLICY=never
COPILOT_BIN=copilot
COPILOT_MODEL=
COPILOT_USE_GH=false
STATUS_WEB_ENABLED=true
STATUS_WEB_HOST=127.0.0.1
STATUS_WEB_PORT=8765
```

### 11. Move From One Bot To Multiple Bots

Mode 1 means:
- each Telegram bot has its own token
- each Telegram bot is pinned to one backend
- one can be Claude, another Codex, another Copilot

Use the example:

```bash
cp ~/myCodeBot/bots.example.json ~/.config/telegram-claude-bridge/bots.json
$EDITOR ~/.config/telegram-claude-bridge/bots.json
```

Example structure:

```json
{
  "bots": [
    {
      "BRIDGE_NAME": "claude_bot",
      "TELEGRAM_BOT_TOKEN": "token-for-claude-bot",
      "BRIDGE_PROVIDER": "claude",
      "CLAUDE_WORKDIR": "~/projects",
      "STATUS_WEB_ENABLED": "true",
      "STATUS_WEB_PORT": "8766"
    },
    {
      "BRIDGE_NAME": "codex_bot",
      "TELEGRAM_BOT_TOKEN": "token-for-codex-bot",
      "BRIDGE_PROVIDER": "codex",
      "CLAUDE_WORKDIR": "~/projects",
      "CODEX_SANDBOX": "danger-full-access",
      "CODEX_APPROVAL_POLICY": "never",
      "STATUS_WEB_ENABLED": "true",
      "STATUS_WEB_PORT": "8765"
    },
    {
      "BRIDGE_NAME": "copilot_bot",
      "TELEGRAM_BOT_TOKEN": "token-for-copilot-bot",
      "BRIDGE_PROVIDER": "copilot",
      "CLAUDE_WORKDIR": "~/projects",
      "COPILOT_USE_GH": "true",
      "STATUS_WEB_ENABLED": "true",
      "STATUS_WEB_PORT": "8767"
    }
  ]
}
```

Then enable multi-bot mode in the env file:

```bash
BOTS_CONFIG_FILE=~/.config/telegram-claude-bridge/bots.json
```

Then restart:

```bash
systemctl --user restart telegram-claude-bridge.service
```

Multi-bot rules:
- every bot must use a different `TELEGRAM_BOT_TOKEN`
- every bot should use a unique `BRIDGE_NAME`
- if multiple bots expose status pages, each must use a different `STATUS_WEB_PORT`
- per-bot stores default to:
  - `data/<bot-name>/sessions.json`
  - `data/<bot-name>/chat_workdirs.json`
  - `data/<bot-name>/approval_prefs.json`
  - `data/<bot-name>/.telegram-media`

### 12. Check Status

Single-bot local status page:

```text
http://127.0.0.1:8765/
http://127.0.0.1:8765/api/status
```

If you set:

```bash
STATUS_WEB_TOKEN=your_secret
```

Then access with:
- `Authorization: Bearer your_secret`
- or `?token=your_secret`

### 13. Common Problems

`claude exited with code 1`
- check `CLAUDE_BIN`
- check `CLAUDE_SETTINGS_FILE`
- run `claude --version`

`codex` keeps failing on shell commands
- use:
  - `CODEX_SANDBOX=danger-full-access`
  - `CODEX_APPROVAL_POLICY=never`

`whisper exited with code -9`
- memory pressure
- use smaller model:
  - `WHISPER_MODEL=base`
  - fallback `tiny`

Telegram command menu does not refresh
- send `/start`
- reopen the chat
- restart the Telegram client

Two bots behave strangely in multi-bot mode
- confirm they do not reuse the same Telegram token
- confirm they do not share the same status port

### 14. Security Notes

- Keep `CLAUDE_WORKDIR` narrow
- expose the bot only to trusted Telegram users unless you have stronger controls
- use `/approve_always` carefully
- use `/approve_bypass` even more carefully
- rotate Telegram bot tokens if they were ever exposed in chat logs

---

<a name="中文"></a>
## 中文

这个项目的作用是：

把 Telegram 变成本机 agent CLI 的远程入口。

当前支持：
- `claude`
- `codex`
- `copilot`
- 单 bot 模式
- 多 bot 模式
- 文本、图片、语音
- 每个 Telegram chat 独立项目目录

下面这份教程按“从零开始”写，一步一步带你做到：
- 先跑通单 bot
- 再在 Telegram 里切项目目录
- 最后同时跑多个 Telegram bots，对接本机 Claude Code 和 Codex

### 1. 先准备好这些东西

在这台机器上先确认已经有：
- Python 3
- `claude` CLI
- 如果要用 Codex，还要有 `codex`
- 如果要用 GitHub Copilot CLI，要有 `copilot` 或 `gh`
- 如果 Claude hook 需要 Node，就要保证 `node` 在 `PATH`
- 如果要转写语音，要有 `whisper`
- 至少一个 Telegram bot token

注意：
- 这个 bridge 不会帮你自动安装 Claude、Codex 或 Whisper
- Telegram 只是聊天入口，真正执行仍然发生在本机

### 2. 克隆项目

```bash
git clone https://github.com/clawwangcai-dev/myCodeBot.git ~/myCodeBot
cd ~/myCodeBot
```

### 3. 准备运行配置

先复制配置文件：

```bash
mkdir -p ~/.config/telegram-claude-bridge
cp ~/myCodeBot/systemd/telegram-claude-bridge.env.example ~/.config/telegram-claude-bridge/env
cp ~/myCodeBot/systemd/telegram-claude-bridge.claude-settings.json ~/.config/telegram-claude-bridge/claude-settings.json
```

编辑 env：

```bash
$EDITOR ~/.config/telegram-claude-bridge/env
```

先填最小单 bot 配置：

```bash
TELEGRAM_BOT_TOKEN=你的_bot_token
BRIDGE_PROVIDER=claude
CLAUDE_WORKDIR=~/projects/safe-repo
CLAUDE_SETTINGS_FILE=~/.config/telegram-claude-bridge/claude-settings.json
CLAUDE_STREAMING=true
```

如果你想先跑 Codex：

```bash
BRIDGE_PROVIDER=codex
CODEX_SANDBOX=danger-full-access
CODEX_APPROVAL_POLICY=never
```

如果你想先跑 GitHub Copilot CLI：

```bash
BRIDGE_PROVIDER=copilot
COPILOT_USE_GH=true
```

安全边界要记住：
- `CLAUDE_WORKDIR` 是默认工作区根目录
- Telegram 里的 `/project` 只能切换到这个根目录下面的子目录

### 4. 启动 bridge

前台直接跑：

```bash
python3 ~/myCodeBot/bot.py
```

安装为后台服务：

```bash
python3 ~/myCodeBot/install_service.py install
python3 ~/myCodeBot/install_service.py restart
python3 ~/myCodeBot/install_service.py status
```

Linux 手动服务方式：

```bash
mkdir -p ~/.config/systemd/user
cp ~/myCodeBot/systemd/telegram-claude-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now telegram-claude-bridge.service
journalctl --user -u telegram-claude-bridge.service -f
```

### 5. 在 Telegram 里测试第一个 bot

先发：

```text
/start
```

再发：

```text
/status
```

然后发一条普通消息：

```text
who are you?
```

### 6. Telegram 命令总览

- `/start` 显示帮助
- `/help` 显示帮助
- `/status` 查看当前 chat 状态
- `/health` 查看 bridge 运行状态
- `/version` 查看 provider 和本机二进制版本
- `/clear` 清除当前会话
- `/project <路径>` 把当前 chat 绑定到项目目录
- `/project_status` 查看当前 chat 的项目目录
- `/approve` 批准当前待授权请求
- `/approve_always` 当前 chat 后续自动批准编辑/写入权限
- `/approve_bypass` 当前 chat 后续自动批准更高权限，包括 Bash/git
- `/approve_manual` 关闭自动批准
- `/deny` 拒绝当前待授权请求

### 7. 在 Telegram 里开始一个新项目

如果你的默认根目录是：

```bash
CLAUDE_WORKDIR=~/projects
```

那你在 Telegram 里就可以直接发：

```text
/project ~/projects/my-new-app
```

然后继续发：

```text
帮我在这里初始化一个 FastAPI 项目
```

`/project` 会做的事：
- 如果目录不存在就创建
- 把当前 Telegram chat 绑定到这个目录
- 清掉旧 session，避免旧项目上下文混进来

`/project` 不允许做的事：
- 切到默认 `CLAUDE_WORKDIR` 之外
- 切到像 `/etc` 这样的任意系统目录

如果要回到默认目录：

```text
/project default
```

### 8. 在 Claude、Codex 和 Copilot 间切换

单 bot 模式下，切换是全局的。

用 Claude：

```bash
BRIDGE_PROVIDER=claude
```

用 Codex：

```bash
BRIDGE_PROVIDER=codex
CODEX_SANDBOX=danger-full-access
CODEX_APPROVAL_POLICY=never
```

用 Copilot：

```bash
BRIDGE_PROVIDER=copilot
COPILOT_USE_GH=true
```

改完后重启服务，然后在 Telegram 发：

```text
/clear
```

这台 Linux 机器上和 Codex 相关的已知点：
- Codex 的 `workspace-write` / `--full-auto` 可能触发 `bwrap: Unknown option --argv0`
- 当前已验证可用的规避方式是：
  - `CODEX_SANDBOX=danger-full-access`
  - `CODEX_APPROVAL_POLICY=never`

### 9. 图片和语音怎么走

当前已经支持：
- 文本
- 图片
- 语音 / 音频

图片流程：
- 从 Telegram 下载到本地
- 再把本地图片路径交给当前后端

语音流程：
- 从 Telegram 下载到本地
- 用本机 `whisper` 转写
- 再把转写文本发给后端

如果语音失败，优先检查：
- `WHISPER_BIN`
- 内存是否够
- 模型是不是太大

推荐的保守配置：

```bash
WHISPER_MODEL=base
WHISPER_FALLBACK_MODELS=tiny
WHISPER_THREADS=2
```

### 10. 单 bot 推荐配置

下面这组配置适合作为起点：

```bash
TELEGRAM_BOT_TOKEN=你的_bot_token
BOTS_CONFIG_FILE=
BRIDGE_PROVIDER=codex
CLAUDE_BIN=claude
CLAUDE_WORKDIR=~/projects
CLAUDE_SETTINGS_FILE=~/.config/telegram-claude-bridge/claude-settings.json
CLAUDE_PERMISSION_MODE=default
CLAUDE_APPROVAL_PERMISSION_MODE=acceptEdits
CLAUDE_TIMEOUT_SECONDS=300
CLAUDE_STREAMING=true
TELEGRAM_POLL_TIMEOUT=30
TELEGRAM_EDIT_INTERVAL_SECONDS=1.0
SESSION_STORE_PATH=~/myCodeBot/sessions.json
WORKDIR_STORE_PATH=~/myCodeBot/chat_workdirs.json
APPROVAL_STORE_PATH=~/myCodeBot/approval_prefs.json
MEDIA_STORE_PATH=~/myCodeBot/.telegram-media
WHISPER_BIN=whisper
WHISPER_MODEL=base
WHISPER_FALLBACK_MODELS=tiny
WHISPER_THREADS=2
CODEX_BIN=codex
CODEX_SANDBOX=danger-full-access
CODEX_APPROVAL_POLICY=never
COPILOT_BIN=copilot
COPILOT_MODEL=
COPILOT_USE_GH=false
STATUS_WEB_ENABLED=true
STATUS_WEB_HOST=127.0.0.1
STATUS_WEB_PORT=8765
```

### 11. 从单 bot 升级到多 bots

模式 1 的含义是：
- 每个 Telegram bot 一个独立 token
- 每个 Telegram bot 固定绑定一个后端
- 一个 bot 可以专门给 Claude
- 另一个 bot 可以专门给 Codex
- 也可以再加一个给 Copilot

先复制示例文件：

```bash
cp ~/myCodeBot/bots.example.json ~/.config/telegram-claude-bridge/bots.json
$EDITOR ~/.config/telegram-claude-bridge/bots.json
```

示例结构：

```json
{
  "bots": [
    {
      "BRIDGE_NAME": "claude_bot",
      "TELEGRAM_BOT_TOKEN": "claude_bot的token",
      "BRIDGE_PROVIDER": "claude",
      "CLAUDE_WORKDIR": "~/projects",
      "STATUS_WEB_ENABLED": "true",
      "STATUS_WEB_PORT": "8766"
    },
    {
      "BRIDGE_NAME": "codex_bot",
      "TELEGRAM_BOT_TOKEN": "codex_bot的token",
      "BRIDGE_PROVIDER": "codex",
      "CLAUDE_WORKDIR": "~/projects",
      "CODEX_SANDBOX": "danger-full-access",
      "CODEX_APPROVAL_POLICY": "never",
      "STATUS_WEB_ENABLED": "true",
      "STATUS_WEB_PORT": "8765"
    },
    {
      "BRIDGE_NAME": "copilot_bot",
      "TELEGRAM_BOT_TOKEN": "copilot_bot的token",
      "BRIDGE_PROVIDER": "copilot",
      "CLAUDE_WORKDIR": "~/projects",
      "COPILOT_USE_GH": "true",
      "STATUS_WEB_ENABLED": "true",
      "STATUS_WEB_PORT": "8767"
    }
  ]
}
```

然后在 env 里启用：

```bash
BOTS_CONFIG_FILE=~/.config/telegram-claude-bridge/bots.json
```

再重启：

```bash
systemctl --user restart telegram-claude-bridge.service
```

多 bot 模式必须满足：
- 每个 bot 的 `TELEGRAM_BOT_TOKEN` 都不同
- 每个 bot 的 `BRIDGE_NAME` 都不同
- 如果都开状态页，每个 bot 的 `STATUS_WEB_PORT` 也必须不同

多 bot 模式下，默认会自动拆分这些存储路径：
- `data/<bot-name>/sessions.json`
- `data/<bot-name>/chat_workdirs.json`
- `data/<bot-name>/approval_prefs.json`
- `data/<bot-name>/.telegram-media`

### 12. 怎么看状态

单 bot 本地状态页：

```text
http://127.0.0.1:8765/
http://127.0.0.1:8765/api/status
```

如果设置了：

```bash
STATUS_WEB_TOKEN=你的密钥
```

访问方式就是：
- `Authorization: Bearer 你的密钥`
- 或 `?token=你的密钥`

### 13. 常见问题

`claude exited with code 1`
- 检查 `CLAUDE_BIN`
- 检查 `CLAUDE_SETTINGS_FILE`
- 跑一下 `claude --version`

Codex 执行 shell 一直失败
- 用：
  - `CODEX_SANDBOX=danger-full-access`
  - `CODEX_APPROVAL_POLICY=never`

`whisper exited with code -9`
- 多半是内存压力
- 改小模型：
  - `WHISPER_MODEL=base`
  - fallback `tiny`

Telegram 里的命令菜单没刷新
- 先发 `/start`
- 退出聊天再进
- 重启 Telegram 客户端

多 bot 模式看起来不正常
- 先确认没有复用同一个 Telegram token
- 再确认状态页端口没有冲突

### 14. 安全提醒

- `CLAUDE_WORKDIR` 尽量设小，不要放太宽
- 如果 Telegram 用户不是完全可信，不要随便放大权限
- `/approve_always` 要谨慎
- `/approve_bypass` 更要谨慎
- 如果 token 曾出现在聊天记录里，建议去 BotFather 旋转
