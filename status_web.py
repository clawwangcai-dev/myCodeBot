from __future__ import annotations

import html
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from config import Settings
from runtime_state import BridgeRuntimeState
from session_store import SessionStore, SessionRecord


LOGGER = logging.getLogger("telegram-claude-bridge.status-web")


def start_status_server(
    settings: Settings,
    store: SessionStore,
    runtime_state: BridgeRuntimeState,
    version_info: dict[str, str],
) -> ThreadingHTTPServer:
    handler_class = _build_handler(settings, store, runtime_state, version_info)
    server = ThreadingHTTPServer((settings.status_web_host, settings.status_web_port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info(
        "Started status web on http://%s:%s",
        settings.status_web_host,
        settings.status_web_port,
    )
    return server


def _build_handler(
    settings: Settings,
    store: SessionStore,
    runtime_state: BridgeRuntimeState,
    version_info: dict[str, str],
):
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/status":
                self._send_json(_status_payload(settings, store, runtime_state, version_info))
                return
            if self.path == "/":
                self._send_html(_render_html(_status_payload(settings, store, runtime_state, version_info)))
                return
            self.send_error(404, "Not Found")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            LOGGER.info("%s - %s", self.address_string(), format % args)

        def _send_json(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return StatusHandler


def _status_payload(
    settings: Settings,
    store: SessionStore,
    runtime_state: BridgeRuntimeState,
    version_info: dict[str, str],
) -> dict[str, Any]:
    snapshot = runtime_state.snapshot()
    sessions = [
        {
            "chat_id": chat_id,
            "session_id": record.session_id,
            "cwd": record.cwd,
            "updated_at": record.updated_at,
        }
        for chat_id, record in store.items()
    ]
    return {
        "service": {
            "name": "telegram-claude-bridge",
            "started_at": snapshot.started_at,
            "last_success_at": snapshot.last_success_at,
            "last_error_at": snapshot.last_error_at,
            "last_error": snapshot.last_error,
            "messages_total": snapshot.messages_total,
            "requests_total": snapshot.requests_total,
            "active_requests": snapshot.active_requests,
        },
        "bridge": {
            "workdir": str(settings.claude_workdir),
            "streaming": settings.claude_streaming,
            "status_web": {
                "enabled": settings.status_web_enabled,
                "host": settings.status_web_host,
                "port": settings.status_web_port,
            },
        },
        "version": version_info,
        "sessions": sessions,
        "session_count": len(sessions),
    }


def _render_html(payload: dict[str, Any]) -> str:
    service = payload["service"]
    bridge = payload["bridge"]
    version = payload["version"]
    sessions = payload["sessions"]

    rows = "\n".join(
        (
            "<tr>"
            f"<td>{html.escape(str(item['chat_id']))}</td>"
            f"<td>{html.escape(item['session_id'])}</td>"
            f"<td>{html.escape(item['updated_at'])}</td>"
            f"<td>{html.escape(item['cwd'])}</td>"
            "</tr>"
        )
        for item in sessions
    ) or '<tr><td colspan="4">No sessions</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Telegram Claude Bridge</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1ea;
      --panel: #fffdf8;
      --ink: #182028;
      --muted: #5d6773;
      --line: #d9d1c2;
      --accent: #0e7490;
    }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      background: radial-gradient(circle at top left, #fff7df, var(--bg) 45%);
      color: var(--ink);
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    h1, h2 {{
      margin: 0 0 12px;
      font-weight: 700;
      letter-spacing: 0.01em;
    }}
    p {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin: 24px 0;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 10px 30px rgba(24, 32, 40, 0.05);
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }}
    .value {{
      font-size: 24px;
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      overflow: hidden;
    }}
    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      background: #f8f2e7;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    code {{
      color: var(--accent);
      font-family: "SFMono-Regular", Consolas, monospace;
    }}
    .meta {{
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Telegram Claude Bridge</h1>
    <p>Local-only status page. JSON endpoint: <code>/api/status</code></p>
    <div class="grid">
      <section class="card"><div class="label">Requests</div><div class="value">{service['requests_total']}</div></section>
      <section class="card"><div class="label">Messages</div><div class="value">{service['messages_total']}</div></section>
      <section class="card"><div class="label">Active Requests</div><div class="value">{service['active_requests']}</div></section>
      <section class="card"><div class="label">Session Count</div><div class="value">{payload['session_count']}</div></section>
    </div>
    <div class="grid">
      <section class="card">
        <h2>Service</h2>
        <div class="meta">
          <div>Started: <code>{html.escape(str(service['started_at']))}</code></div>
          <div>Last success: <code>{html.escape(str(service['last_success_at']))}</code></div>
          <div>Last error: <code>{html.escape(str(service['last_error'] or 'none'))}</code></div>
        </div>
      </section>
      <section class="card">
        <h2>Version</h2>
        <div class="meta">
          <div>Git: <code>{html.escape(version['git_commit'])}</code></div>
          <div>Claude: <code>{html.escape(version['claude_version'])}</code></div>
          <div>Python: <code>{html.escape(version['python'])}</code></div>
          <div>Platform: <code>{html.escape(version['platform'])}</code></div>
        </div>
      </section>
      <section class="card">
        <h2>Bridge</h2>
        <div class="meta">
          <div>Workdir: <code>{html.escape(bridge['workdir'])}</code></div>
          <div>Streaming: <code>{html.escape(str(bridge['streaming']))}</code></div>
          <div>Status web: <code>{html.escape(str(bridge['status_web']['host']))}:{bridge['status_web']['port']}</code></div>
        </div>
      </section>
    </div>
    <section>
      <h2>Sessions</h2>
      <table>
        <thead>
          <tr><th>Chat ID</th><th>Session ID</th><th>Updated</th><th>CWD</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""
