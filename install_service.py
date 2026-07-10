from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


SERVICE_NAME = "telegram-claude-bridge"
REPO_DIR = Path(__file__).resolve().parent
PYTHON_BIN = Path(sys.executable).resolve()
CLAUDE_SETTINGS_TEMPLATE = REPO_DIR / "systemd" / "telegram-claude-bridge.claude-settings.json"


def run(command: list[str], *, check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def detect_platform() -> str:
    system = platform.system()
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    raise RuntimeError(f"Unsupported platform: {system}")


def detect_existing_command(*candidates: str) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def config_dir_for(target: str) -> Path:
    home = Path.home()
    if target == "linux":
        return home / ".config" / SERVICE_NAME
    if target == "macos":
        return home / "Library" / "Application Support" / SERVICE_NAME
    return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / SERVICE_NAME


def env_path_for(target: str) -> Path:
    return config_dir_for(target) / "env"


def default_path_prefix(target: str) -> str:
    home = Path.home()
    if target == "linux":
        return ":".join(
            [
                str(home / ".local" / "bin"),
                "/home/linuxbrew/.linuxbrew/bin",
                "/home/linuxbrew/.linuxbrew/sbin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            ]
        )
    if target == "macos":
        return ":".join(
            [
                str(home / ".local" / "bin"),
                "/opt/homebrew/bin",
                "/opt/homebrew/sbin",
                "/usr/local/bin",
                "/usr/local/sbin",
                "/usr/bin",
                "/bin",
            ]
        )
    local_appdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    return ";".join(
        [
            str(local_appdata / "Programs" / "Python" / "Python313"),
            str(local_appdata / "Programs" / "Python" / "Python312"),
            str(local_appdata / "Microsoft" / "WindowsApps"),
        ]
    )


def ensure_env_file(target: str, config_dir: Path) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    env_path = config_dir / "env"
    settings_path = config_dir / "claude-settings.json"
    whisper_bin = detect_existing_command("whisper")

    if not settings_path.exists():
        shutil.copyfile(CLAUDE_SETTINGS_TEMPLATE, settings_path)

    if env_path.exists():
        return env_path

    env_content = textwrap.dedent(
        f"""\
        TELEGRAM_BOT_TOKEN=
        BOTS_CONFIG_FILE=
        BRIDGE_PROVIDER=claude
        CLAUDE_BIN=claude
        CLAUDE_WORKDIR={REPO_DIR}
        CLAUDE_SETTINGS_FILE={settings_path}
        CLAUDE_PERMISSION_MODE=default
        CLAUDE_ALLOWED_TOOLS=
        CLAUDE_DISALLOWED_TOOLS=
        CLAUDE_TIMEOUT_SECONDS=300
        CLAUDE_STREAMING=true
        TELEGRAM_POLL_TIMEOUT=30
        TELEGRAM_EDIT_INTERVAL_SECONDS=1.0
        SESSION_STORE_PATH={REPO_DIR / "sessions.json"}
        WORKDIR_STORE_PATH={REPO_DIR / "chat_workdirs.json"}
        WHISPER_BIN={whisper_bin or 'whisper'}
        WHISPER_MODEL=base
        WHISPER_FALLBACK_MODELS=tiny
        WHISPER_LANGUAGE=
        WHISPER_THREADS=2
        CODEX_BIN=codex
        CODEX_MODEL=
        CODEX_MODELS=gpt-5.6
        CODEX_SANDBOX=workspace-write
        CODEX_APPROVAL_POLICY=on-request
        COPILOT_BIN=copilot
        COPILOT_MODEL=
        COPILOT_USE_GH=false
        BRIDGE_PATH_PREFIX={default_path_prefix(target)}
        """
    )
    env_path.write_text(env_content, encoding="utf-8")
    return env_path


def linux_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"com.{SERVICE_NAME}.plist"


def windows_task_name() -> str:
    return SERVICE_NAME


def install_linux(env_path: Path, *, start: bool) -> None:
    service_path = linux_service_path()
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_content = textwrap.dedent(
        f"""\
        [Unit]
        Description=Telegram Agent CLI Bridge
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        WorkingDirectory={REPO_DIR}
        Environment=PYTHONUNBUFFERED=1
        ExecStart={PYTHON_BIN} {REPO_DIR / "service_entry.py"} --env {env_path}
        Restart=on-failure
        RestartSec=3
        TimeoutStopSec=20

        [Install]
        WantedBy=default.target
        """
    )
    service_path.write_text(service_content, encoding="utf-8")
    run(["systemctl", "--user", "daemon-reload"])
    if start:
        run(["systemctl", "--user", "enable", f"{SERVICE_NAME}.service"])
        run(["systemctl", "--user", "restart", f"{SERVICE_NAME}.service"])


def install_macos(env_path: Path, *, start: bool) -> None:
    plist_path = macos_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    logs_dir = config_dir_for("macos") / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    plist_content = textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key>
          <string>com.{SERVICE_NAME}</string>
          <key>ProgramArguments</key>
          <array>
            <string>{PYTHON_BIN}</string>
            <string>{REPO_DIR / "service_entry.py"}</string>
            <string>--env</string>
            <string>{env_path}</string>
          </array>
          <key>WorkingDirectory</key>
          <string>{REPO_DIR}</string>
          <key>RunAtLoad</key>
          <true/>
          <key>KeepAlive</key>
          <true/>
          <key>StandardOutPath</key>
          <string>{logs_dir / "stdout.log"}</string>
          <key>StandardErrorPath</key>
          <string>{logs_dir / "stderr.log"}</string>
        </dict>
        </plist>
        """
    )
    plist_path.write_text(plist_content, encoding="utf-8")
    if start:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)], check=False)
        run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)])
        run(["launchctl", "enable", f"gui/{os.getuid()}/com.{SERVICE_NAME}"])
        run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.{SERVICE_NAME}"])


def install_windows(env_path: Path, *, start: bool) -> None:
    task_command = f'"{PYTHON_BIN}" "{REPO_DIR / "service_entry.py"}" --env "{env_path}"'
    run(
        [
            "schtasks",
            "/Create",
            "/TN",
            windows_task_name(),
            "/SC",
            "ONLOGON",
            "/TR",
            task_command,
            "/F",
        ]
    )
    if start:
        run(["schtasks", "/Run", "/TN", windows_task_name()])


def install_service(target: str, *, start: bool) -> None:
    config_dir = config_dir_for(target)
    env_path = ensure_env_file(target, config_dir)

    if target == "linux":
        install_linux(env_path, start=start)
    elif target == "macos":
        install_macos(env_path, start=start)
    else:
        install_windows(env_path, start=start)

    print(f"Installed {SERVICE_NAME} for {target}.")
    print(f"Env file: {env_path}")
    print("Fill TELEGRAM_BOT_TOKEN if it is still empty.")


def service_control(target: str, action: str) -> None:
    if target == "linux":
        if action == "start":
            run(["systemctl", "--user", "start", f"{SERVICE_NAME}.service"])
        elif action == "stop":
            run(["systemctl", "--user", "stop", f"{SERVICE_NAME}.service"])
        else:
            run(["systemctl", "--user", "restart", f"{SERVICE_NAME}.service"])
        return

    if target == "macos":
        label = f"gui/{os.getuid()}/com.{SERVICE_NAME}"
        plist_path = macos_plist_path()
        if action == "start":
            run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)])
            run(["launchctl", "enable", label])
            run(["launchctl", "kickstart", "-k", label])
        elif action == "stop":
            run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)])
        else:
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)], check=False)
            run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)])
            run(["launchctl", "enable", label])
            run(["launchctl", "kickstart", "-k", label])
        return

    if action == "start":
        run(["schtasks", "/Run", "/TN", windows_task_name()])
    elif action == "stop":
        print("Windows Task Scheduler cannot force-stop the running task cleanly from this installer.")
    else:
        subprocess.run(["schtasks", "/End", "/TN", windows_task_name()], check=False)
        run(["schtasks", "/Run", "/TN", windows_task_name()])


def status_service(target: str) -> int:
    env_path = env_path_for(target)
    print(f"Platform: {target}")
    print(f"Env file: {env_path}")
    print(f"Env file exists: {env_path.exists()}")

    if target == "linux":
        result = run(
            ["systemctl", "--user", "status", f"{SERVICE_NAME}.service", "--no-pager"],
            check=False,
            capture_output=True,
        )
        output = result.stdout or result.stderr
        print(output.rstrip() or f"Service status exit code: {result.returncode}")
        return result.returncode

    if target == "macos":
        result = run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.{SERVICE_NAME}"],
            check=False,
            capture_output=True,
        )
        output = result.stdout or result.stderr
        print(output.rstrip() or f"launchctl exit code: {result.returncode}")
        return result.returncode

    result = run(
        ["schtasks", "/Query", "/TN", windows_task_name(), "/FO", "LIST", "/V"],
        check=False,
        capture_output=True,
    )
    output = result.stdout or result.stderr
    print(output.rstrip() or f"schtasks exit code: {result.returncode}")
    return result.returncode


def uninstall_service(target: str) -> None:
    if target == "linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"], check=False)
        service_path = linux_service_path()
        if service_path.exists():
            service_path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    elif target == "macos":
        plist_path = macos_plist_path()
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)], check=False)
        if plist_path.exists():
            plist_path.unlink()
    else:
        subprocess.run(["schtasks", "/Delete", "/TN", windows_task_name(), "/F"], check=False)

    print(f"Uninstalled {SERVICE_NAME} for {target}.")


# ---------------------------------------------------------------------------
# Interactive init command
# ---------------------------------------------------------------------------

OBSIDIAN_SKILL_DIR = REPO_DIR / ".claude" / "skills" / "voice-bridge-obsidian"
OBSIDIAN_SETTINGS_EXAMPLE = OBSIDIAN_SKILL_DIR / "config" / "settings.example.yaml"
OBSIDIAN_SETTINGS_YAML = OBSIDIAN_SKILL_DIR / "config" / "settings.yaml"
ENV_TEMPLATE = REPO_DIR / "systemd" / "telegram-claude-bridge.env.example"


def _bi(en: str, zh: str) -> str:
    """Bilingual prompt helper."""
    return f"{en} / {zh}"


def _parse_env_lines(text: str) -> dict[str, str]:
    """Parse KEY=VALUE pairs from env file text, ignoring comments/blanks."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _merge_env_lines(existing_text: str, overrides: dict[str, str]) -> str:
    """Merge overrides into existing env file text, preserving comments and order."""
    lines = existing_text.splitlines()
    result: list[str] = []
    written_keys: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            result.append(line)
            continue
        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in overrides:
            result.append(f"{key}={overrides[key]}")
            written_keys.add(key)
        else:
            result.append(line)

    for key, value in overrides.items():
        if key not in written_keys:
            result.append(f"{key}={value}")

    return "\n".join(result) + "\n"


def _prompt_input(label: str, *, default: str = "", required: bool = False) -> str:
    """Prompt for a single value with optional default and required validation."""
    prompt_text = f"  {label}"
    if default:
        prompt_text += f" [{default}]"
    prompt_text += ": "

    while True:
        raw = input(prompt_text).strip()
        if raw:
            return raw
        if default:
            return default
        if not required:
            return ""
        print("    " + _bi("This value is required.", "此项为必填项。"))


def _prompt_yes_no(label: str, *, default: bool = False) -> bool:
    """Prompt for a yes/no answer."""
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {label} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "是")


def _interactive_prompt(existing: dict[str, str]) -> dict[str, str]:
    """Walk the user through essential config values. Returns env var overrides."""
    overrides: dict[str, str] = {}

    print()
    print("=" * 50)
    print(_bi("Bau-Dirigent Setup", "Bau-Dirigent 初始化配置"))
    print("=" * 50)
    print(f"  {_bi('Detected platform', '检测到平台')}: {detect_platform()}")
    print()

    # Step 1: TELEGRAM_BOT_TOKEN (required)
    current_token = existing.get("TELEGRAM_BOT_TOKEN", "")
    if current_token:
        print(f"  TELEGRAM_BOT_TOKEN: {_bi('already set', '已设置')} ({current_token[:8]}...)")
    else:
        print(f"  {_bi('Step 1: Telegram Bot Token (required)', '步骤 1: Telegram Bot Token（必填）')}")
        print(f"    {_bi('Get it from @BotFather on Telegram', '从 Telegram 的 @BotFather 获取')}")
        token = _prompt_input("TELEGRAM_BOT_TOKEN", required=True)
        overrides["TELEGRAM_BOT_TOKEN"] = token

    # Step 2: BRIDGE_PROVIDER
    current_provider = existing.get("BRIDGE_PROVIDER", "claude")
    print(f"\n  {_bi('Step 2: Bridge provider', '步骤 2: 桥接后端')}")
    print("    1) claude   (Claude Code CLI)")
    print("    2) codex    (OpenAI Codex CLI)")
    print("    3) copilot  (GitHub Copilot CLI)")
    choice = _prompt_input(
        _bi("Choose", "选择"),
        default=current_provider,
    )
    provider_map = {"1": "claude", "2": "codex", "3": "copilot"}
    provider = provider_map.get(choice, choice)
    if provider != current_provider:
        overrides["BRIDGE_PROVIDER"] = provider

    # Step 3: CLAUDE_WORKDIR
    current_workdir = existing.get("CLAUDE_WORKDIR", str(REPO_DIR))
    print(f"\n  {_bi('Step 3: Default workspace', '步骤 3: 默认工作区')}")
    print(f"    {_bi('Agent operates in this directory', 'Agent 在此目录下执行操作')}")
    workdir = _prompt_input("CLAUDE_WORKDIR", default=current_workdir)
    resolved = str(Path(workdir).expanduser().resolve())
    if resolved != current_workdir:
        overrides["CLAUDE_WORKDIR"] = resolved

    # Step 4: Obsidian skill
    print(f"\n  {_bi('Step 4: Obsidian voice-bridge skill', '步骤 4: Obsidian 语音桥接技能')}")
    print(f"    {_bi('Enables voice transcription and note archiving into an Obsidian vault', '启用语音转写和笔记归档到 Obsidian vault')}")
    do_obsidian = _prompt_yes_no(
        _bi("Set up Obsidian skill?", "设置 Obsidian 技能？"),
        default=False,
    )
    if do_obsidian:
        current_vault = existing.get("OBSIDIAN_VAULT_PATH", "")
        vault_path = _prompt_input("OBSIDIAN_VAULT_PATH", default=current_vault, required=True)
        resolved_vault = str(Path(vault_path).expanduser().resolve())
        overrides["OBSIDIAN_VAULT_PATH"] = resolved_vault

        if not resolved_vault or not Path(resolved_vault).is_dir():
            print(f"    ⚠ {_bi('Path does not exist, will be created on first use', '路径不存在，首次使用时会自动创建')}")

        if OBSIDIAN_SETTINGS_EXAMPLE.exists() and not OBSIDIAN_SETTINGS_YAML.exists():
            shutil.copyfile(OBSIDIAN_SETTINGS_EXAMPLE, OBSIDIAN_SETTINGS_YAML)
            print(f"    {_bi('Bootstrapped', '已生成')} settings.yaml {_bi('from example template', '（基于示例模板）')}")

    return overrides


def _noninteractive_defaults(existing: dict[str, str]) -> dict[str, str]:
    """Compute defaults for --no-input mode. Returns env var overrides."""
    overrides: dict[str, str] = {}

    token = existing.get("TELEGRAM_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN is required but not set.", file=sys.stderr)
        print("Set it in the env file or via environment variable.", file=sys.stderr)
        raise SystemExit(1)

    if "BRIDGE_PROVIDER" not in existing:
        overrides["BRIDGE_PROVIDER"] = "claude"
    if "CLAUDE_WORKDIR" not in existing:
        overrides["CLAUDE_WORKDIR"] = str(REPO_DIR)

    return overrides


def run_init(*, no_input: bool = False) -> Path:
    """Run the interactive (or non-interactive) first-time setup.

    Returns the path to the generated .env file.
    """
    env_path = REPO_DIR / ".env"

    # Load existing values
    existing: dict[str, str] = {}
    if env_path.exists():
        existing = _parse_env_lines(env_path.read_text(encoding="utf-8"))
    else:
        # Also check platform config dir for existing env
        platform_env = env_path_for(detect_platform())
        if platform_env.exists():
            existing = _parse_env_lines(platform_env.read_text(encoding="utf-8"))

    # Gather overrides
    if no_input:
        overrides = _noninteractive_defaults(existing)
    else:
        overrides = _interactive_prompt(existing)

    if not overrides:
        print(_bi("\nNo changes needed.", "\n无需修改。"))
        return env_path

    # Summary
    print()
    print("=" * 50)
    print(_bi("Summary / 配置摘要", "配置摘要"))
    print("=" * 50)
    for key, value in overrides.items():
        display = value if len(value) < 50 else value[:47] + "..."
        print(f"  {key} = {display}")
    print(f"  {_bi('Config file', '配置文件')}: {env_path}")

    # Confirm
    if not no_input:
        if not _prompt_yes_no(_bi("Write config?", "写入配置？"), default=True):
            print(_bi("Aborted.", "已取消。"))
            return env_path

    # Generate / update .env
    if env_path.exists():
        original = env_path.read_text(encoding="utf-8")
        merged = _merge_env_lines(original, overrides)
    elif ENV_TEMPLATE.exists():
        template_text = ENV_TEMPLATE.read_text(encoding="utf-8")
        merged = _merge_env_lines(template_text, overrides)
    else:
        lines = [f"{k}={v}" for k, v in overrides.items()]
        merged = "\n".join(lines) + "\n"

    env_path.write_text(merged, encoding="utf-8")

    # Also sync to platform config dir (used by install_service / launchd / systemd)
    target = detect_platform()
    platform_env = env_path_for(target)
    platform_dir = platform_env.parent
    platform_dir.mkdir(parents=True, exist_ok=True)
    if platform_env.exists():
        platform_original = platform_env.read_text(encoding="utf-8")
        platform_merged = _merge_env_lines(platform_original, overrides)
    else:
        platform_merged = merged
    platform_env.write_text(platform_merged, encoding="utf-8")

    print()
    print(_bi("Done! Configuration written to", "完成！配置已写入"), env_path)
    print(f"  {_bi('Also synced to', '已同步到')}: {platform_env}")
    print()
    print(_bi("Next steps:", "下一步："))
    print(f"  1. {_bi('Review config', '检查配置')}: $EDITOR {env_path}")
    print(f"  2. {_bi('Start in foreground', '前台运行')}: set -a && source .env && set +a && python3 bot.py")
    print(f"  3. {_bi('Or install as service', '或安装为后台服务')}: python3 install_service.py install")

    return env_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the Telegram agent bridge background service.")
    parser.add_argument("--platform", choices=["linux", "macos", "windows"], help="Override platform detection.")

    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="Install the background service.")
    install_parser.add_argument("--no-start", action="store_true", help="Install but do not start.")

    init_parser = subparsers.add_parser("init", help="Interactive first-time setup / 交互式初始化配置")
    init_parser.add_argument("--no-input", action="store_true", help="Non-interactive: use defaults, no prompts")

    subparsers.add_parser("status", help="Show service status.")
    subparsers.add_parser("uninstall", help="Remove the installed service.")
    subparsers.add_parser("start", help="Start the installed service.")
    subparsers.add_parser("stop", help="Stop the installed service.")
    subparsers.add_parser("restart", help="Restart the installed service.")

    parser.set_defaults(command="install", no_start=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = args.platform or detect_platform()

    if args.command == "init":
        run_init(no_input=getattr(args, "no_input", False))
        return
    if args.command == "install":
        install_service(target, start=not args.no_start)
        return
    if args.command == "status":
        raise SystemExit(status_service(target))
    if args.command == "uninstall":
        uninstall_service(target)
        return
    if args.command in {"start", "stop", "restart"}:
        service_control(target, args.command)
        print(f"{args.command.capitalize()}ed {SERVICE_NAME} for {target}.")
        return
    raise RuntimeError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
