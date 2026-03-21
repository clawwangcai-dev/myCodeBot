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

    if not settings_path.exists():
        shutil.copyfile(CLAUDE_SETTINGS_TEMPLATE, settings_path)

    if env_path.exists():
        return env_path

    env_content = textwrap.dedent(
        f"""\
        TELEGRAM_BOT_TOKEN=
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
        Description=Telegram Claude CLI Bridge
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the Telegram Claude bridge background service.")
    parser.add_argument("--platform", choices=["linux", "macos", "windows"], help="Override platform detection.")

    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="Install the background service.")
    install_parser.add_argument("--no-start", action="store_true", help="Install but do not start.")

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
