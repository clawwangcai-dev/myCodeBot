from __future__ import annotations

import html
import json
import logging
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from channel_keys import ConversationRef, parse_conversation_key
from chat_log import ChatLogStore
from config import Settings
from construction_agent import ConstructionAgentService
from codex_usage import load_codex_usage
from resume_telegram_session import get_resume_targets_for_chat
from runtime_state import BridgeRuntimeState
from session_store import SessionStore
from workdir_store import WorkdirStore


LOGGER = logging.getLogger("telegram-claude-bridge.status-web")


def start_status_server(
    settings: Settings,
    store: SessionStore,
    workdirs: WorkdirStore,
    approvals,
    runtime_state: BridgeRuntimeState,
    version_info: dict[str, str],
    chat_log: ChatLogStore,
    construction_agent: ConstructionAgentService | None,
    submit_prompt,
) -> ThreadingHTTPServer:
    handler_class = _build_handler(
        settings,
        store,
        workdirs,
        approvals,
        runtime_state,
        version_info,
        chat_log,
        construction_agent,
        submit_prompt,
    )
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
    workdirs: WorkdirStore,
    approvals,
    runtime_state: BridgeRuntimeState,
    version_info: dict[str, str],
    chat_log: ChatLogStore,
    construction_agent: ConstructionAgentService | None,
    submit_prompt,
):
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not _is_authorized(settings, self.headers.get("Authorization"), parsed.query):
                self._send_unauthorized()
                return

            if parsed.path == "/api/status":
                self._send_json(
                    _status_payload(settings, store, workdirs, approvals, runtime_state, version_info, chat_log, construction_agent)
                )
                return
            if parsed.path == "/api/chats":
                self._send_json(_chat_list_payload(store, workdirs, approvals, chat_log))
                return
            if parsed.path == "/api/chat":
                conversation = _parse_conversation(
                    parse_qs(parsed.query).get("conversation_key", [None])[0],
                    parse_qs(parsed.query).get("chat_id", [None])[0],
                )
                if conversation is None:
                    self.send_error(400, "Missing or invalid conversation_key")
                    return
                self._send_json(_chat_payload(conversation, store, workdirs, approvals, chat_log))
                return
            if parsed.path == "/api/construction/overview":
                if construction_agent is None or not construction_agent.enabled:
                    self.send_error(404, "Construction agent not enabled")
                    return
                work_date = parse_qs(parsed.query).get("date", [None])[0]
                self._send_json({"ok": True, "data": construction_agent.overview(work_date=work_date)})
                return
            if parsed.path == "/api/construction/resources":
                if construction_agent is None or not construction_agent.enabled:
                    self.send_error(404, "Construction agent not enabled")
                    return
                kind = str(parse_qs(parsed.query).get("kind", [""])[0]).strip()
                if not kind:
                    self.send_error(400, "kind is required")
                    return
                self._send_json({"ok": True, "data": construction_agent.list_resources(kind)})
                return
            if parsed.path == "/api/construction/notes":
                if construction_agent is None or not construction_agent.enabled:
                    self.send_error(404, "Construction agent not enabled")
                    return
                status = parse_qs(parsed.query).get("status", [None])[0]
                self._send_json({"ok": True, "data": construction_agent.list_notes(status=status, limit=100)})
                return
            if parsed.path == "/":
                self._send_html(
                    _render_status_html(
                        _status_payload(settings, store, workdirs, approvals, runtime_state, version_info, chat_log, construction_agent)
                    )
                )
                return
            if parsed.path == "/chat":
                self._send_html(_render_chat_html(settings))
                return
            if parsed.path == "/construction":
                if construction_agent is None or not construction_agent.enabled:
                    self.send_error(404, "Construction agent not enabled")
                    return
                self._send_html(_render_construction_html(settings))
                return
            self.send_error(404, "Not Found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not _is_authorized(settings, self.headers.get("Authorization"), parsed.query):
                self._send_unauthorized()
                return
            payload = self._read_json_body()
            if parsed.path == "/api/chat/send":
                conversation = _parse_conversation(payload.get("conversation_key"), payload.get("chat_id"))
                prompt = str(payload.get("prompt") or "").strip()
                mirror_to_telegram = bool(payload.get("mirror_to_telegram", True))
                if conversation is None or not prompt:
                    self.send_error(400, "conversation_key and prompt are required")
                    return

                submit_prompt(conversation.key, prompt, mirror_to_telegram=mirror_to_telegram)
                self._send_json({"ok": True, "conversation_key": conversation.key, "queued": True})
                return
            if construction_agent is None or not construction_agent.enabled:
                self.send_error(404, "Not Found")
                return
            if parsed.path == "/api/construction/resource":
                kind = str(payload.get("kind") or "").strip()
                record = payload.get("record")
                if not kind or not isinstance(record, dict):
                    self.send_error(400, "kind and record are required")
                    return
                self._send_json({"ok": True, "data": construction_agent.save_resource(kind, record)})
                return
            if parsed.path == "/api/construction/confirm-note":
                note_id = str(payload.get("note_id") or "").strip()
                actor = str(payload.get("actor") or "web").strip()
                if not note_id:
                    self.send_error(400, "note_id is required")
                    return
                self._send_json({"ok": True, "data": construction_agent.confirm_note(note_id, actor=actor)})
                return
            if parsed.path == "/api/construction/plan/generate":
                work_date = str(payload.get("work_date") or "").strip() or None
                actor = str(payload.get("actor") or "web").strip()
                plan = construction_agent.generate_plan(work_date=work_date, created_reason="web-generate", created_by=actor)
                self._send_json({"ok": True, "data": plan})
                return
            if parsed.path == "/api/construction/plan/replan":
                reason = str(payload.get("reason") or "").strip()
                work_date = str(payload.get("work_date") or "").strip() or None
                actor = str(payload.get("actor") or "web").strip()
                if not reason:
                    self.send_error(400, "reason is required")
                    return
                self._send_json({"ok": True, "data": construction_agent.replan(reason=reason, work_date=work_date, actor=actor)})
                return
            if parsed.path == "/api/construction/override":
                plan_id = str(payload.get("plan_id") or "").strip()
                assignment_id = str(payload.get("assignment_id") or "").strip()
                if not plan_id or not assignment_id:
                    self.send_error(400, "plan_id and assignment_id are required")
                    return
                self._send_json(
                    {
                        "ok": True,
                        "data": construction_agent.apply_override(
                            plan_id=plan_id,
                            assignment_id=assignment_id,
                            new_employee_names=payload.get("new_employee_names") or [],
                            new_vehicle_code=str(payload.get("new_vehicle_code") or "").strip() or None,
                            changed_by=str(payload.get("changed_by") or "web").strip(),
                            reason_type=str(payload.get("reason_type") or "manual_override").strip(),
                            reason_text=str(payload.get("reason_text") or "").strip(),
                            should_learn=bool(payload.get("should_learn", False)),
                        ),
                    }
                )
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

        def _send_unauthorized(self) -> None:
            encoded = b"Unauthorized"
            self.send_response(401)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("WWW-Authenticate", 'Bearer realm="telegram-claude-bridge-status"')
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

    return StatusHandler


def _is_authorized(settings: Settings, authorization_header: str | None, query: str) -> bool:
    expected = settings.status_web_token
    if not expected:
        return True

    if authorization_header:
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() == "bearer" and token == expected:
            return True

    params = parse_qs(query, keep_blank_values=False)
    query_tokens = params.get("token") or []
    return expected in query_tokens


def _status_payload(
    settings: Settings,
    store: SessionStore,
    workdirs: WorkdirStore,
    approvals,
    runtime_state: BridgeRuntimeState,
    version_info: dict[str, str],
    chat_log: ChatLogStore,
    construction_agent: ConstructionAgentService | None,
) -> dict[str, Any]:
    snapshot = runtime_state.snapshot()
    sessions = []
    for conversation_key, record in store.items():
        conversation = parse_conversation_key(conversation_key)
        usage = load_codex_usage(record.session_id) if settings.provider == "codex" else None
        sessions.append(
            {
                "conversation_key": conversation.key,
                "channel": conversation.channel,
                "chat_id": conversation.chat_id,
                "session_id": record.session_id,
                "cwd": record.cwd,
                "updated_at": record.updated_at,
                "codex_usage": usage.to_dict() if usage is not None else None,
            }
        )
    return {
        "service": {
            "name": settings.name,
            "started_at": snapshot.started_at,
            "last_success_at": snapshot.last_success_at,
            "last_error_at": snapshot.last_error_at,
            "last_error": snapshot.last_error,
            "messages_total": snapshot.messages_total,
            "requests_total": snapshot.requests_total,
            "active_requests": snapshot.active_requests,
        },
        "bridge": {
            "provider": settings.provider,
            "workdir": str(settings.claude_workdir),
            "streaming": settings.claude_streaming,
            "approval_store_path": str(settings.approval_store_path),
            "workdir_store_path": str(settings.workdir_store_path),
            "approve_always_chats": approvals.always_count(),
            "project_override_chats": len(workdirs.items()),
            "construction_agent": {
                "enabled": bool(construction_agent and construction_agent.enabled),
                "db_path": str(construction_agent.db_path) if construction_agent and construction_agent.enabled else None,
            },
            "status_web": {
                "enabled": settings.status_web_enabled,
                "host": settings.status_web_host,
                "port": settings.status_web_port,
            },
        },
        "version": version_info,
        "workdir_overrides": [
            {
                "conversation_key": parse_conversation_key(conversation_key).key,
                "channel": parse_conversation_key(conversation_key).channel,
                "chat_id": parse_conversation_key(conversation_key).chat_id,
                "cwd": cwd,
            }
            for conversation_key, cwd in workdirs.items()
        ],
        "sessions": sessions,
        "session_count": len(sessions),
        "chat_count": len(_known_conversations(store, workdirs, chat_log)),
    }


def _known_conversations(store: SessionStore, workdirs: WorkdirStore, chat_log: ChatLogStore) -> list[ConversationRef]:
    keys = {conversation_key for conversation_key, _ in store.items()}
    keys.update(conversation_key for conversation_key, _ in workdirs.items())
    keys.update(chat_log.conversation_keys())
    return sorted((parse_conversation_key(key) for key in keys), key=lambda item: item.key)


def _chat_list_payload(
    store: SessionStore,
    workdirs: WorkdirStore,
    approvals,
    chat_log: ChatLogStore,
) -> dict[str, Any]:
    chats = []
    for conversation in _known_conversations(store, workdirs, chat_log):
        record = store.get(conversation.key)
        chats.append(
            {
                "conversation_key": conversation.key,
                "channel": conversation.channel,
                "chat_id": conversation.chat_id,
                "session_id": record.session_id if record else None,
                "updated_at": record.updated_at if record else None,
                "cwd": record.cwd if record else workdirs.get(conversation.key),
                "pending_approval": approvals.get(conversation.key) is not None,
                "message_count": len(chat_log.items(conversation.key, limit=0)),
            }
        )
    return {"chats": chats}


def _chat_payload(
    conversation: ConversationRef,
    store: SessionStore,
    workdirs: WorkdirStore,
    approvals,
    chat_log: ChatLogStore,
) -> dict[str, Any]:
    record = store.get(conversation.key)
    resume_targets = get_resume_targets_for_chat(conversation.key)
    messages = [
        {
            "id": item.id,
            "channel": item.channel,
            "chat_id": item.chat_id,
            "role": item.role,
            "source": item.source,
            "text": item.text,
            "created_at": item.created_at,
        }
        for item in chat_log.items(conversation.key)
    ]
    return {
        "conversation_key": conversation.key,
        "channel": conversation.channel,
        "chat_id": conversation.chat_id,
        "session_id": record.session_id if record else None,
        "updated_at": record.updated_at if record else None,
        "cwd": record.cwd if record else workdirs.get(conversation.key),
        "pending_approval": approvals.get(conversation.key) is not None,
        "resume_targets": [
            {
                "bot": target.settings.name,
                "provider": target.settings.provider,
                "session_id": target.record.session_id,
                "cwd": target.record.cwd,
                "command": shlex.join(target.command),
            }
            for target in resume_targets
        ],
        "messages": messages,
    }


def _parse_conversation(conversation_key: Any, chat_id: Any) -> ConversationRef | None:
    if conversation_key is not None and str(conversation_key).strip():
        return parse_conversation_key(str(conversation_key).strip())
    if chat_id is None:
        return None
    text = str(chat_id).strip()
    if not text:
        return None
    return parse_conversation_key(text)


def _render_status_html(payload: dict[str, Any]) -> str:
    service = payload["service"]
    bridge = payload["bridge"]
    version = payload["version"]
    sessions = payload["sessions"]
    construction = bridge.get("construction_agent") or {}
    construction_line = (
        ' Construction: <a href="/construction">/construction</a>'
        if construction.get("enabled")
        else ""
    )

    rows = "\n".join(
        (
            "<tr>"
            f"<td>{html.escape(str(item['channel']))}</td>"
            f"<td>{html.escape(str(item['chat_id']))}</td>"
            f"<td>{html.escape(item['session_id'])}</td>"
            f"<td>{html.escape(item['updated_at'])}</td>"
            f"<td>{html.escape(item['cwd'])}</td>"
            "</tr>"
        )
        for item in sessions
    ) or '<tr><td colspan="5">No sessions</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local Agent Bridge</title>
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
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(service['name'])}</h1>
    <p>Local-only status page. JSON endpoint: <code>/api/status</code>. Chat UI: <a href="/chat">/chat</a>.{construction_line}</p>
    <div class="grid">
      <section class="card"><div class="label">Requests</div><div class="value">{service['requests_total']}</div></section>
      <section class="card"><div class="label">Messages</div><div class="value">{service['messages_total']}</div></section>
      <section class="card"><div class="label">Active Requests</div><div class="value">{service['active_requests']}</div></section>
      <section class="card"><div class="label">Session Count</div><div class="value">{payload['session_count']}</div></section>
      <section class="card"><div class="label">Chat Count</div><div class="value">{payload['chat_count']}</div></section>
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
          <div>Provider: <code>{html.escape(version['provider'])}</code></div>
          <div>Claude: <code>{html.escape(version['claude_version'])}</code></div>
          <div>Codex: <code>{html.escape(version['codex_version'])}</code></div>
          <div>Copilot: <code>{html.escape(version['copilot_version'])}</code></div>
          <div>Python: <code>{html.escape(version['python'])}</code></div>
          <div>Platform: <code>{html.escape(version['platform'])}</code></div>
        </div>
      </section>
      <section class="card">
        <h2>Bridge</h2>
        <div class="meta">
          <div>Provider: <code>{html.escape(bridge['provider'])}</code></div>
          <div>Workdir: <code>{html.escape(bridge['workdir'])}</code></div>
          <div>Streaming: <code>{html.escape(str(bridge['streaming']))}</code></div>
          <div>Status web: <code>{html.escape(str(bridge['status_web']['host']))}:{bridge['status_web']['port']}</code></div>
          <div>Construction agent: <code>{html.escape(str(construction.get('enabled', False)))}</code></div>
        </div>
      </section>
    </div>
    <section>
      <h2>Sessions</h2>
      <table>
        <thead>
          <tr><th>Channel</th><th>Chat ID</th><th>Session ID</th><th>Updated</th><th>CWD</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def _render_construction_html(settings: Settings) -> str:
    title = html.escape(settings.name)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Construction Ops - {title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #efe7da;
      --panel: rgba(255, 253, 248, 0.94);
      --ink: #1c2228;
      --muted: #616f7b;
      --line: #d4cab6;
      --accent: #b45309;
      --accent-2: #0f766e;
      --danger: #b91c1c;
      --warn: #b45309;
      --ok: #0f766e;
      --cream: #fff8ef;
      --paper: #fffdf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      background:
        radial-gradient(circle at top right, rgba(255, 231, 183, 0.8), transparent 32%),
        linear-gradient(180deg, #f8f0e4, var(--bg));
      color: var(--ink);
    }}
    main {{
      max-width: 1320px;
      margin: 0 auto;
      padding: 24px 20px 36px;
      display: grid;
      gap: 18px;
    }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(28, 34, 40, 0.05);
    }}
    .hero {{
      display: grid;
      gap: 10px;
      grid-template-columns: 1.4fr 1fr;
    }}
    .hero h1, .panel h2 {{ margin: 0; }}
    .subtle {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 18px;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 10px;
      margin-top: 14px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      background: var(--cream);
    }}
    .metric .label {{
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 6px;
    }}
    .metric .value {{
      font-size: 24px;
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{
      color: var(--muted);
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.08em;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 10px 14px;
      font: inherit;
      color: white;
      background: var(--accent);
      cursor: pointer;
    }}
    button.secondary {{ background: var(--accent-2); }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px 12px;
      font: inherit;
      background: white;
      color: inherit;
    }}
    textarea {{
      min-height: 220px;
      resize: vertical;
    }}
    .row {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      align-items: end;
    }}
    .button-row {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .toolbar {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      align-items: end;
      margin-bottom: 14px;
    }}
    .toolbar-field {{
      display: grid;
      gap: 6px;
    }}
    .toolbar-note {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      font-family: "SFMono-Regular", Consolas, monospace;
      background: #fff9f0;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
    }}
    details {{
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--paper);
      overflow: hidden;
    }}
    details summary {{
      cursor: pointer;
      padding: 12px 14px;
      font-weight: 700;
      background: #fbf5ea;
    }}
    details > pre {{
      border: 0;
      border-top: 1px solid var(--line);
      border-radius: 0;
    }}
    .plan-stage {{
      display: grid;
      gap: 16px;
    }}
    .plan-header {{
      display: grid;
      gap: 16px;
      grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.9fr);
      align-items: start;
    }}
    .plan-banner {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      background:
        linear-gradient(135deg, rgba(245, 158, 11, 0.12), rgba(15, 118, 110, 0.08)),
        var(--paper);
    }}
    .plan-date {{
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .plan-title {{
      font-size: 32px;
      line-height: 1.05;
      margin: 0 0 8px;
    }}
    .plan-summary-text {{
      color: var(--muted);
      line-height: 1.5;
      margin: 0;
    }}
    .issue-card {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      background: var(--paper);
      display: grid;
      gap: 12px;
    }}
    .issue-section {{
      display: grid;
      gap: 8px;
    }}
    .issue-title {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-weight: 700;
    }}
    .chip-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 13px;
      border: 1px solid transparent;
      background: #f5efe3;
      color: var(--ink);
    }}
    .chip.risk {{
      background: rgba(185, 28, 28, 0.08);
      color: var(--danger);
      border-color: rgba(185, 28, 28, 0.18);
    }}
    .chip.gap {{
      background: rgba(180, 83, 9, 0.08);
      color: var(--warn);
      border-color: rgba(180, 83, 9, 0.18);
    }}
    .chip.ok {{
      background: rgba(15, 118, 110, 0.08);
      color: var(--ok);
      border-color: rgba(15, 118, 110, 0.18);
    }}
    .plan-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
    }}
    .assignment-card {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0.72), rgba(255,248,239,0.98));
      display: grid;
      gap: 12px;
      transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
    }}
    .assignment-card.is-selected {{
      border-color: rgba(15, 118, 110, 0.42);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.08);
      transform: translateY(-1px);
    }}
    .assignment-top {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 12px;
    }}
    .assignment-site {{
      font-size: 24px;
      line-height: 1.05;
      margin: 0;
    }}
    .assignment-meta {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }}
    .score-badge {{
      min-width: 82px;
      border-radius: 14px;
      padding: 10px 12px;
      background: rgba(15, 118, 110, 0.1);
      color: var(--ok);
      text-align: center;
      border: 1px solid rgba(15, 118, 110, 0.15);
    }}
    .score-badge .score-label {{
      display: block;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 10px;
      margin-bottom: 4px;
    }}
    .score-badge .score-value {{
      font-size: 22px;
      font-weight: 700;
    }}
    .crew-list, .reason-list {{
      display: grid;
      gap: 8px;
    }}
    .crew-member {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(245, 239, 227, 0.78);
      border: 1px solid var(--line);
      font-size: 14px;
    }}
    .crew-member strong {{
      font-size: 15px;
    }}
    .crew-note {{
      color: var(--muted);
      font-size: 12px;
    }}
    .vehicle-card {{
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(221, 244, 255, 0.58);
      border: 1px solid rgba(14, 116, 144, 0.16);
    }}
    .vehicle-card.missing {{
      background: rgba(185, 28, 28, 0.05);
      border-color: rgba(185, 28, 28, 0.16);
      color: var(--danger);
    }}
    .reason-list li {{
      margin-left: 18px;
      line-height: 1.45;
      color: #39424c;
    }}
    .assignment-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 4px;
    }}
    .assignment-actions button {{
      padding: 9px 12px;
    }}
    .site-search {{
      letter-spacing: 0.01em;
    }}
    .view-caption {{
      margin: 2px 0 14px;
      color: var(--muted);
      font-size: 13px;
    }}
    .no-print {{
      display: initial;
    }}
    .empty-state {{
      border: 1px dashed var(--line);
      border-radius: 18px;
      padding: 28px 20px;
      text-align: center;
      color: var(--muted);
      background: rgba(255, 253, 248, 0.72);
    }}
    @media (max-width: 960px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .row {{ grid-template-columns: 1fr; }}
      .toolbar {{ grid-template-columns: 1fr; }}
      .plan-header {{ grid-template-columns: 1fr; }}
    }}
    @media print {{
      body {{
        background: white;
      }}
      .hero,
      .no-print,
      details,
      .panel:not(.print-keep) {{
        display: none !important;
      }}
      main {{
        max-width: none;
        padding: 0;
      }}
      .panel {{
        border: 0;
        box-shadow: none;
        padding: 0;
        background: white;
      }}
      .plan-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .assignment-card,
      .issue-card,
      .plan-banner {{
        background: white;
        break-inside: avoid;
        page-break-inside: avoid;
        box-shadow: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div>
        <h1>Construction Ops Console</h1>
        <p class="subtle">Bridge: {title}. Use this page to inspect registry data, trigger planning, confirm pending notes, and apply manual overrides.</p>
        <div class="button-row">
          <button id="refreshOverview">Refresh Overview</button>
          <button class="secondary" id="generatePlan">Generate Today Plan</button>
          <button class="secondary" id="replanButton">Replan From Reason</button>
          <button class="secondary no-print" id="printPlan">Print Day Report</button>
        </div>
      </div>
      <section id="overviewBox" class="metric-grid">
        <div class="metric"><div class="label">Loading</div><div class="value">…</div></div>
      </section>
    </section>

    <section class="grid">
      <article class="panel">
        <h2>Registry</h2>
        <div class="row">
          <label>Kind<select id="resourceKind">
            <option value="employees">Employees</option>
            <option value="sites">Sites</option>
            <option value="requirements">Requirements</option>
            <option value="vehicles">Vehicles</option>
            <option value="rules">Rules</option>
          </select></label>
          <label>Work Date<input id="workDate" placeholder="YYYY-MM-DD"></label>
          <label>Actor<input id="actorInput" value="web"></label>
          <button type="button" id="loadResources">Load</button>
        </div>
        <textarea id="resourceEditor" placeholder='Paste a JSON object here, for example {{"name":"新员工","role_type":"木工"}}'></textarea>
        <div class="button-row">
          <button type="button" id="saveResource">Save Resource</button>
        </div>
        <pre id="resourceBox">No resource loaded yet.</pre>
      </article>

      <article class="panel">
        <h2>Pending Notes</h2>
        <div class="row">
          <label>Note ID<input id="noteIdInput" placeholder="note id"></label>
          <button type="button" id="loadNotes">Load Pending</button>
          <button type="button" class="secondary" id="confirmNote">Confirm Note</button>
        </div>
        <pre id="notesBox">No pending notes loaded yet.</pre>
      </article>
    </section>

    <section class="grid">
      <article class="panel print-keep">
        <h2>Latest Plan</h2>
        <div class="toolbar no-print">
          <label class="toolbar-field">Sort View
            <select id="planSort">
              <option value="fit_desc">Highest Fit Score</option>
              <option value="risk_first">Risk First</option>
              <option value="gap_first">Gap First</option>
              <option value="site_name">Site Name</option>
            </select>
          </label>
          <label class="toolbar-field">Filter View
            <select id="planFilter">
              <option value="all">Show All Sites</option>
              <option value="attention_only">Only Need Attention</option>
              <option value="risk_only">Only Risks</option>
              <option value="gap_only">Only Gaps</option>
            </select>
          </label>
          <label class="toolbar-field">Search
            <input id="planSearch" class="site-search" placeholder="Search site, employee, vehicle">
          </label>
          <button type="button" class="secondary" id="resetPlanView">Reset View</button>
        </div>
        <p class="toolbar-note no-print">先看卡片，再决定是否改排。点击任一工地卡片里的按钮，会把当前班组和车辆带到右侧 override 表单。</p>
        <p id="planViewCaption" class="view-caption no-print">Current view: all sites.</p>
        <section id="planBox" class="plan-stage">
          <div class="empty-state">Plan output will appear here.</div>
        </section>
      </article>
      <article class="panel">
        <h2>Overrides</h2>
        <p id="overrideHint" class="subtle">Click “Prepare Override” on any site card to prefill this form.</p>
        <div class="row">
          <label>Plan ID<input id="overridePlanId" placeholder="plan id"></label>
          <label>Assignment ID<input id="overrideAssignmentId" placeholder="assignment id"></label>
          <label>Employees<input id="overrideEmployees" placeholder="老周,小王"></label>
          <label>Vehicle<input id="overrideVehicle" placeholder="V01"></label>
        </div>
        <div class="row">
          <label>Reason Type<input id="overrideReasonType" value="manual_override"></label>
          <label>Reason Text<input id="overrideReasonText" placeholder="why changed"></label>
          <label>Should Learn<select id="overrideLearn"><option value="false">false</option><option value="true">true</option></select></label>
          <button type="button" id="applyOverride">Apply Override</button>
        </div>
        <pre id="overrideBox">No overrides applied in this session.</pre>
      </article>
    </section>

    <section class="panel">
      <h2>Replan</h2>
      <textarea id="replanReason" placeholder="例如：老王今天请假，7号车故障，重新排班。"></textarea>
    </section>
  </main>
  <script>
    const querySuffix = window.location.search || "";

    async function apiGet(path) {{
      const response = await fetch(path + querySuffix);
      return response.json();
    }}

    async function apiPost(path, payload) {{
      const response = await fetch(path + querySuffix, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload),
      }});
      return response.json();
    }}

    function pretty(value) {{
      return JSON.stringify(value, null, 2);
    }}

    function escapeHtml(value) {{
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }}

    function metricCard(label, value) {{
      return `
        <section class="metric">
          <div class="label">${{escapeHtml(label)}}</div>
          <div class="value">${{escapeHtml(value)}}</div>
        </section>
      `;
    }}

    function renderOverview(data) {{
      if (!data) {{
        overviewBox.innerHTML = metricCard("Overview", "N/A");
        return;
      }}
      const counts = data.counts || {{}};
      overviewBox.innerHTML = [
        metricCard("Date", data.work_date || "today"),
        metricCard("Employees", counts.employees ?? 0),
        metricCard("Sites", counts.sites ?? 0),
        metricCard("Today Needs", counts.requirements ?? 0),
        metricCard("Vehicles", counts.vehicles ?? 0),
        metricCard("Pending Notes", counts.pending_notes ?? 0),
      ].join("");
    }}

    function summarizeCrew(memberNames) {{
      if (!memberNames || memberNames.length === 0) {{
        return "待补位";
      }}
      return memberNames.join(" + ");
    }}

    function renderCrewMembers(assignment, byName) {{
      const memberNames = assignment.employee_names || [];
      if (!memberNames.length) {{
        return `<div class="crew-member"><strong>待补位</strong><span class="crew-note">当前没有满足约束的班组</span></div>`;
      }}
      return memberNames.map(name => {{
        const employee = byName.get(name);
        const noteBits = [];
        if (employee?.primary_skill) noteBits.push(employee.primary_skill);
        if (employee?.can_lead_team) noteBits.push("可带队");
        if (employee?.can_drive) noteBits.push("可开车");
        if (employee?.certificates?.length) noteBits.push(employee.certificates.join(" / "));
        return `
          <div class="crew-member">
            <div>
              <strong>${{escapeHtml(name)}}</strong>
              <div class="crew-note">${{escapeHtml(noteBits.join(" · ") || "现场作业"))}}</div>
            </div>
          </div>
        `;
      }}).join("");
    }}

    function renderIssueChips(items, type, emptyText) {{
      if (!items || items.length === 0) {{
        return `<span class="chip ok">${{escapeHtml(emptyText)}}</span>`;
      }}
      return items.map(item => `<span class="chip ${{type}}">${{escapeHtml(item)}}</span>`).join("");
    }}

    function assignmentHasGap(assignment) {{
      const employeeNames = assignment?.employee_names || [];
      return !employeeNames.length || !assignment?.vehicle;
    }}

    function planNeedsAttention(assignment) {{
      return assignmentHasGap(assignment) || ((assignment?.risk_flags || []).length > 0);
    }}

    function getPlanViewState() {{
      return {{
        sort: document.getElementById("planSort").value,
        filter: document.getElementById("planFilter").value,
        search: document.getElementById("planSearch").value.trim().toLowerCase(),
      }};
    }}

    function sortAssignments(assignments, mode) {{
      const items = [...assignments];
      const compareScore = (left, right) => Number(right.score || 0) - Number(left.score || 0);
      if (mode === "risk_first") {{
        items.sort((left, right) => {{
          const riskDelta = (right.risk_flags || []).length - (left.risk_flags || []).length;
          if (riskDelta !== 0) return riskDelta;
          const gapDelta = Number(assignmentHasGap(right)) - Number(assignmentHasGap(left));
          if (gapDelta !== 0) return gapDelta;
          return compareScore(left, right);
        }});
        return items;
      }}
      if (mode === "gap_first") {{
        items.sort((left, right) => {{
          const gapDelta = Number(assignmentHasGap(right)) - Number(assignmentHasGap(left));
          if (gapDelta !== 0) return gapDelta;
          const riskDelta = (right.risk_flags || []).length - (left.risk_flags || []).length;
          if (riskDelta !== 0) return riskDelta;
          return compareScore(left, right);
        }});
        return items;
      }}
      if (mode === "site_name") {{
        items.sort((left, right) => String(left.site_name || "").localeCompare(String(right.site_name || ""), "zh-Hans-CN"));
        return items;
      }}
      items.sort(compareScore);
      return items;
    }}

    function filterAssignments(assignments, viewState) {{
      return assignments.filter(assignment => {{
        if (viewState.filter === "attention_only" && !planNeedsAttention(assignment)) {{
          return false;
        }}
        if (viewState.filter === "risk_only" && (assignment.risk_flags || []).length === 0) {{
          return false;
        }}
        if (viewState.filter === "gap_only" && !assignmentHasGap(assignment)) {{
          return false;
        }}
        if (!viewState.search) {{
          return true;
        }}
        const searchHaystack = [
          assignment.site_name || "",
          ...(assignment.employee_names || []),
          assignment.vehicle?.vehicle_code || "",
          ...(assignment.risk_flags || []),
        ].join(" ").toLowerCase();
        return searchHaystack.includes(viewState.search);
      }});
    }}

    function setPlanViewCaption(totalCount, visibleCount, viewState) {{
      const modeLabel = {{
        fit_desc: "按匹配分排序",
        risk_first: "风险优先",
        gap_first: "缺口优先",
        site_name: "按工地名称排序",
      }}[viewState.sort] || "当前排序";
      const filterLabel = {{
        all: "全部工地",
        attention_only: "只看需关注工地",
        risk_only: "只看风险工地",
        gap_only: "只看缺口工地",
      }}[viewState.filter] || "全部工地";
      const searchLabel = viewState.search ? `，搜索“${{escapeHtml(viewState.search)}}”` : "";
      planViewCaption.innerHTML = `当前视图：${{escapeHtml(filterLabel)}}，${{escapeHtml(modeLabel)}}，显示 ${{escapeHtml(visibleCount)}} / ${{escapeHtml(totalCount)}} 个工地${{searchLabel}}。`;
    }}

    function prefillOverrideForm(planId, assignmentId, employeeNames, vehicleCode, siteName) {{
      document.getElementById("overridePlanId").value = planId || "";
      document.getElementById("overrideAssignmentId").value = assignmentId || "";
      document.getElementById("overrideEmployees").value = (employeeNames || []).join(",");
      document.getElementById("overrideVehicle").value = vehicleCode || "";
      if (!document.getElementById("overrideReasonText").value.trim()) {{
        document.getElementById("overrideReasonText").value = siteName ? `调整 ${{siteName}} 今日排班` : "";
      }}
      window.__selectedAssignmentId = assignmentId || "";
      overrideHint.textContent = siteName ? `正在编辑：${{siteName}}。可以直接修改人员、车辆和原因。` : "Override draft ready.";
      if (window.__latestConstructionPlan) {{
        renderPlan(window.__latestConstructionPlan);
      }}
      document.getElementById("overrideEmployees").focus();
      document.getElementById("overrideEmployees").scrollIntoView({{ behavior: "smooth", block: "center" }});
    }}

    function rerenderLatestPlan() {{
      if (window.__latestConstructionPlan) {{
        renderPlan(window.__latestConstructionPlan);
      }}
    }}

    function renderPlan(plan) {{
      if (!plan || !plan.assignments || plan.assignments.length === 0) {{
        window.__latestConstructionPlan = plan || null;
        setPlanViewCaption(0, 0, getPlanViewState());
        planBox.innerHTML = `<div class="empty-state">No draft plan yet. Generate today’s plan to see the owner view.</div>`;
        return;
      }}
      window.__latestConstructionPlan = plan;
      const summary = plan.summary || {{}};
      const risks = summary.risks || [];
      const gaps = summary.gaps || [];
      const allEmployees = Array.isArray(window.__constructionEmployees) ? window.__constructionEmployees : [];
      const byName = new Map(allEmployees.map(item => [item.name, item]));
      const viewState = getPlanViewState();
      const visibleAssignments = sortAssignments(filterAssignments(plan.assignments, viewState), viewState.sort);
      const hiddenCount = Math.max(0, plan.assignments.length - visibleAssignments.length);
      setPlanViewCaption(plan.assignments.length, visibleAssignments.length, viewState);
      const assignmentCards = visibleAssignments.map(assignment => {{
        const vehicle = assignment.vehicle;
        const explanation = assignment.explanation || {{}};
        const factors = explanation.factors || [];
        const riskFlags = assignment.risk_flags || [];
        const selectedClass = assignment.id === window.__selectedAssignmentId ? " is-selected" : "";
        return `
          <article class="assignment-card${{selectedClass}}">
            <div class="assignment-top">
              <div>
                <div class="plan-date">Site Assignment</div>
                <h3 class="assignment-site">${{escapeHtml(assignment.site_name || "Unnamed Site")}}</h3>
                <div class="assignment-meta">
                  班组：${{escapeHtml(summarizeCrew(assignment.employee_names || []))}}<br>
                  工地编号：${{escapeHtml(assignment.site_id || "n/a")}}
                </div>
              </div>
              <div class="score-badge">
                <span class="score-label">Fit Score</span>
                <span class="score-value">${{escapeHtml(Number(assignment.score || 0).toFixed(1))}}</span>
              </div>
            </div>

            <div class="crew-list">
              ${{renderCrewMembers(assignment, byName)}}
            </div>

            <div class="vehicle-card${{vehicle ? "" : " missing"}}">
              <strong>Vehicle</strong><br>
              ${{vehicle ? escapeHtml(vehicle.vehicle_code || vehicle.id || "assigned") : "未分配车辆"}}
            </div>

            <div>
              <div class="label">Why This Works</div>
              <ul class="reason-list">
                ${{(factors.length ? factors : ["当前没有详细解释因子"]).map(item => `<li>${{escapeHtml(item)}}</li>`).join("")}}
              </ul>
            </div>

            <div class="chip-row">
              ${{renderIssueChips(riskFlags, "risk", "当前无单独风险提示")}}
            </div>

            <div class="assignment-actions no-print">
              <button
                type="button"
                class="secondary prefill-override"
                data-plan-id="${{escapeHtml(plan.id || "")}}"
                data-assignment-id="${{escapeHtml(assignment.id || "")}}"
                data-site-name="${{escapeHtml(assignment.site_name || "")}}"
                data-employees="${{escapeHtml((assignment.employee_names || []).join(","))}}"
                data-vehicle="${{escapeHtml(vehicle?.vehicle_code || "")}}"
              >Prepare Override</button>
            </div>
          </article>
        `;
      }}).join("");

      planBox.innerHTML = `
        <section class="plan-header">
          <article class="plan-banner">
            <div class="plan-date">${{escapeHtml(plan.work_date || "today")}} · Draft Dispatch</div>
            <h3 class="plan-title">今日排班总览</h3>
            <p class="plan-summary-text">
              今日共安排 ${{escapeHtml(summary.assignment_count ?? plan.assignments.length)}} 个工地，
              风险 ${{escapeHtml(summary.risk_count ?? risks.length)}} 项，
              缺口 ${{escapeHtml(summary.gap_count ?? gaps.length)}} 项。
              下面每张卡片就是一个工地当天的建议安排${{hiddenCount ? `；当前视图隐藏了 ${{hiddenCount}} 个工地` : ""}}。
            </p>
            <div class="metric-grid">
              ${{metricCard("Sites Assigned", summary.assignment_count ?? plan.assignments.length)}}
              ${{metricCard("Risks", summary.risk_count ?? risks.length)}}
              ${{metricCard("Gaps", summary.gap_count ?? gaps.length)}}
              ${{metricCard("Generated", (plan.generated_at || "").replace("T", " ").slice(0, 16) || "now")}}
            </div>
          </article>

          <article class="issue-card">
            <section class="issue-section">
              <div class="issue-title">
                <span>Key Risks</span>
                <span class="chip risk">${{escapeHtml(risks.length)}}</span>
              </div>
              <div class="chip-row">${{renderIssueChips(risks.slice(0, 8), "risk", "当前没有高风险提示")}}</div>
            </section>

            <section class="issue-section">
              <div class="issue-title">
                <span>Staffing Gaps</span>
                <span class="chip gap">${{escapeHtml(gaps.length)}}</span>
              </div>
              <div class="chip-row">${{renderIssueChips(gaps.slice(0, 8), "gap", "当前没有缺口")}}</div>
            </section>
          </article>
        </section>

        <section class="plan-grid">
          ${{assignmentCards || `<div class="empty-state">当前筛选条件下没有匹配工地。请放宽过滤条件再看。</div>`}}
        </section>

        <details>
          <summary>Raw Plan JSON</summary>
          <pre>${{escapeHtml(pretty(plan))}}</pre>
        </details>
      `;
    }}

    const overviewBox = document.getElementById("overviewBox");
    const resourceBox = document.getElementById("resourceBox");
    const planBox = document.getElementById("planBox");
    const planViewCaption = document.getElementById("planViewCaption");
    const notesBox = document.getElementById("notesBox");
    const overrideBox = document.getElementById("overrideBox");
    const overrideHint = document.getElementById("overrideHint");
    window.__constructionEmployees = [];
    window.__latestConstructionPlan = null;
    window.__selectedAssignmentId = "";

    async function refreshOverview() {{
      const payload = await apiGet("/api/construction/overview");
      renderOverview(payload.data);
      if (payload.data.latest_plan) {{
        renderPlan(payload.data.latest_plan);
      }}
    }}

    async function loadResources() {{
      const kind = document.getElementById("resourceKind").value;
      const payload = await apiGet(`/api/construction/resources?kind=${{encodeURIComponent(kind)}}`);
      resourceBox.textContent = pretty(payload.data);
      if (kind === "employees" && Array.isArray(payload.data)) {{
        window.__constructionEmployees = payload.data;
      }}
      const sample = Array.isArray(payload.data) && payload.data.length ? payload.data[0] : {{}};
      document.getElementById("resourceEditor").value = pretty(sample);
    }}

    async function saveResource() {{
      const kind = document.getElementById("resourceKind").value;
      const raw = document.getElementById("resourceEditor").value.trim();
      const record = raw ? JSON.parse(raw) : {{}};
      const payload = await apiPost("/api/construction/resource", {{ kind, record }});
      resourceBox.textContent = pretty(payload.data);
      await refreshOverview();
    }}

    async function loadNotes() {{
      const payload = await apiGet("/api/construction/notes?status=pending_review");
      notesBox.textContent = pretty(payload.data);
    }}

    async function confirmNote() {{
      const noteId = document.getElementById("noteIdInput").value.trim();
      if (!noteId) {{
        return;
      }}
      const actor = document.getElementById("actorInput").value.trim() || "web";
      const payload = await apiPost("/api/construction/confirm-note", {{ note_id: noteId, actor }});
      notesBox.textContent = pretty(payload.data);
      await refreshOverview();
    }}

    async function generatePlan() {{
      const workDate = document.getElementById("workDate").value.trim();
      const actor = document.getElementById("actorInput").value.trim() || "web";
      const payload = await apiPost("/api/construction/plan/generate", {{ work_date: workDate || null, actor }});
      renderPlan(payload.data);
      document.getElementById("overridePlanId").value = payload.data.id || "";
      await refreshOverview();
    }}

    async function replan() {{
      const reason = document.getElementById("replanReason").value.trim();
      const workDate = document.getElementById("workDate").value.trim();
      const actor = document.getElementById("actorInput").value.trim() || "web";
      if (!reason) {{
        return;
      }}
      const payload = await apiPost("/api/construction/plan/replan", {{ reason, work_date: workDate || null, actor }});
      renderPlan(payload.data.plan);
      document.getElementById("overridePlanId").value = payload.data.plan.id || "";
      await refreshOverview();
    }}

    async function applyOverride() {{
      const payload = await apiPost("/api/construction/override", {{
        plan_id: document.getElementById("overridePlanId").value.trim(),
        assignment_id: document.getElementById("overrideAssignmentId").value.trim(),
        new_employee_names: document.getElementById("overrideEmployees").value.split(",").map(item => item.trim()).filter(Boolean),
        new_vehicle_code: document.getElementById("overrideVehicle").value.trim() || null,
        changed_by: document.getElementById("actorInput").value.trim() || "web",
        reason_type: document.getElementById("overrideReasonType").value.trim() || "manual_override",
        reason_text: document.getElementById("overrideReasonText").value.trim(),
        should_learn: document.getElementById("overrideLearn").value === "true",
      }});
      const assignment = payload.data?.assignment || {{}};
      window.__selectedAssignmentId = assignment.id || window.__selectedAssignmentId;
      overrideHint.textContent = assignment.site_name
        ? `已更新：${{assignment.site_name}}。可继续修改或重新生成今日方案。`
        : "Override applied.";
      overrideBox.textContent = [
        `Override ID: ${{payload.data.override_id || "n/a"}}`,
        `Site: ${{assignment.site_name || "n/a"}}`,
        `Crew: ${{(assignment.employee_names || []).join(" + ") || "待补位"}}`,
        `Vehicle: ${{assignment.vehicle?.vehicle_code || "未分配"}}`,
      ].join("\\n");
      await refreshOverview();
    }}

    function printCurrentPlan() {{
      if (!window.__latestConstructionPlan) {{
        planBox.innerHTML = `<div class="empty-state">请先生成今日排班，再打印日报。</div>`;
        return;
      }}
      window.print();
    }}

    document.getElementById("refreshOverview").addEventListener("click", refreshOverview);
    document.getElementById("loadResources").addEventListener("click", loadResources);
    document.getElementById("saveResource").addEventListener("click", saveResource);
    document.getElementById("loadNotes").addEventListener("click", loadNotes);
    document.getElementById("confirmNote").addEventListener("click", confirmNote);
    document.getElementById("generatePlan").addEventListener("click", generatePlan);
    document.getElementById("replanButton").addEventListener("click", replan);
    document.getElementById("applyOverride").addEventListener("click", applyOverride);
    document.getElementById("printPlan").addEventListener("click", printCurrentPlan);
    document.getElementById("planSort").addEventListener("change", rerenderLatestPlan);
    document.getElementById("planFilter").addEventListener("change", rerenderLatestPlan);
    document.getElementById("planSearch").addEventListener("input", rerenderLatestPlan);
    document.getElementById("resetPlanView").addEventListener("click", () => {{
      document.getElementById("planSort").value = "fit_desc";
      document.getElementById("planFilter").value = "all";
      document.getElementById("planSearch").value = "";
      rerenderLatestPlan();
    }});
    planBox.addEventListener("click", event => {{
      const trigger = event.target.closest(".prefill-override");
      if (!trigger) {{
        return;
      }}
      const employees = String(trigger.dataset.employees || "").split(",").map(item => item.trim()).filter(Boolean);
      prefillOverrideForm(
        trigger.dataset.planId || "",
        trigger.dataset.assignmentId || "",
        employees,
        trigger.dataset.vehicle || "",
        trigger.dataset.siteName || "",
      );
    }});

    refreshOverview();
    loadResources();
    loadNotes();
    apiGet("/api/construction/resources?kind=employees").then(payload => {{
      if (payload && Array.isArray(payload.data)) {{
        window.__constructionEmployees = payload.data;
      }}
    }});
  </script>
</body>
</html>"""


def _render_chat_html(settings: Settings) -> str:
    title = html.escape(settings.name)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local Chat - {title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3efe7;
      --panel: #fffdfa;
      --ink: #1d252d;
      --muted: #64707c;
      --line: #d7cfbf;
      --accent: #0f766e;
      --user: #ddf4ff;
      --assistant: #f6f0d8;
      --system: #f2f2f2;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      background:
        radial-gradient(circle at top left, #fff7de 0, rgba(255, 247, 222, 0.7) 28%, transparent 52%),
        linear-gradient(180deg, #f7f2e9, var(--bg));
      color: var(--ink);
    }}
    .layout {{
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      min-height: 100vh;
    }}
    aside {{
      border-right: 1px solid var(--line);
      padding: 20px;
      background: rgba(255, 253, 248, 0.9);
      backdrop-filter: blur(6px);
    }}
    main {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100vh;
    }}
    h1, h2 {{ margin: 0; }}
    .subtle {{ color: var(--muted); }}
    .chat-list {{
      display: grid;
      gap: 10px;
      margin-top: 18px;
    }}
    .chat-item {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 14px;
      padding: 12px;
      cursor: pointer;
      text-align: left;
    }}
    .chat-item.active {{
      border-color: var(--accent);
      box-shadow: 0 10px 24px rgba(15, 118, 110, 0.12);
    }}
    .topbar {{
      padding: 20px 24px 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.8);
    }}
    .resume-strip {{
      margin-top: 14px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .resume-card {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px 12px;
      background: var(--panel);
      min-width: 220px;
    }}
    .resume-card button {{
      margin-top: 8px;
      padding: 8px 12px;
      font-size: 14px;
    }}
    .messages {{
      padding: 22px 24px 28px;
      overflow: auto;
      display: grid;
      gap: 14px;
    }}
    .bubble {{
      max-width: min(820px, 90%);
      border-radius: 18px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      white-space: pre-wrap;
      line-height: 1.45;
      box-shadow: 0 12px 28px rgba(29, 37, 45, 0.04);
    }}
    .bubble.user {{
      justify-self: end;
      background: var(--user);
    }}
    .bubble.assistant {{
      justify-self: start;
      background: var(--assistant);
    }}
    .bubble.system {{
      justify-self: center;
      background: var(--system);
      max-width: 760px;
    }}
    .meta {{
      margin-bottom: 6px;
      font: 12px/1.3 "SFMono-Regular", Consolas, monospace;
      color: var(--muted);
    }}
    form {{
      border-top: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.92);
      padding: 16px 24px 22px;
    }}
    textarea, input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      font: inherit;
      background: white;
      color: inherit;
    }}
    textarea {{
      min-height: 110px;
      resize: vertical;
      margin-top: 10px;
    }}
    .row {{
      display: grid;
      gap: 12px;
      grid-template-columns: 1fr auto;
      align-items: center;
    }}
    .controls {{
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      padding: 12px 18px;
      font: inherit;
      cursor: pointer;
    }}
    label {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 14px;
    }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <h1>Local Chat</h1>
      <p class="subtle">Bridge: {title}</p>
      <input id="conversationInput" placeholder="conversation key, e.g. telegram:12345">
      <div class="chat-list" id="chatList"></div>
    </aside>
    <main>
      <section class="topbar">
        <h2 id="chatTitle">No chat selected</h2>
        <p class="subtle" id="chatMeta">Pick an existing chat or enter a conversation key manually.</p>
        <div class="resume-strip" id="resumeStrip"></div>
      </section>
      <section class="messages" id="messages"></section>
      <form id="composer">
        <div class="controls">
          <label><input type="checkbox" id="mirrorToggle" checked> mirror desktop message to remote chat</label>
          <button type="submit">Send</button>
        </div>
        <textarea id="promptInput" placeholder="Type a message for the shared chat..."></textarea>
      </form>
    </main>
  </div>
  <script>
    const state = {{
      currentConversationKey: null,
      lastMessageKey: "",
    }};
    const querySuffix = window.location.search || "";

    const chatListEl = document.getElementById("chatList");
    const conversationInputEl = document.getElementById("conversationInput");
    const chatTitleEl = document.getElementById("chatTitle");
    const chatMetaEl = document.getElementById("chatMeta");
    const resumeStripEl = document.getElementById("resumeStrip");
    const messagesEl = document.getElementById("messages");
    const promptInputEl = document.getElementById("promptInput");
    const mirrorToggleEl = document.getElementById("mirrorToggle");
    const composerEl = document.getElementById("composer");

    function escapeHtml(value) {{
      return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }}

    function setCurrentChat(conversationKey) {{
      state.currentConversationKey = String(conversationKey).trim() || null;
      state.lastMessageKey = "";
      conversationInputEl.value = state.currentConversationKey || "";
      refreshChatList();
      refreshChat();
    }}

    async function copyText(value) {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        await navigator.clipboard.writeText(value);
        return;
      }}
      window.prompt("Copy command:", value);
    }}

    function renderResumeTargets(items) {{
      if (!items || items.length === 0) {{
        resumeStripEl.innerHTML = "";
        return;
      }}
      resumeStripEl.innerHTML = items.map(item => `
        <section class="resume-card">
          <div><strong>${{escapeHtml(item.provider)}}</strong> · ${{escapeHtml(item.bot)}}</div>
          <div class="subtle">${{escapeHtml(item.cwd || "")}}</div>
          <button type="button" data-command="${{escapeHtml(item.command)}}">Copy resume command</button>
        </section>
      `).join("");
      for (const button of resumeStripEl.querySelectorAll("button[data-command]")) {{
        button.addEventListener("click", async () => {{
          await copyText(button.getAttribute("data-command") || "");
        }});
      }}
    }}

    async function refreshChatList() {{
      const response = await fetch("/api/chats" + querySuffix);
      const payload = await response.json();
      chatListEl.innerHTML = "";
      for (const item of payload.chats) {{
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chat-item" + (item.conversation_key === state.currentConversationKey ? " active" : "");
        button.innerHTML = `
          <div><strong>${{escapeHtml(item.channel)}} · ${{escapeHtml(item.chat_id)}}</strong></div>
          <div class="subtle">${{escapeHtml(item.conversation_key)}}</div>
          <div class="subtle">${{escapeHtml(item.cwd || "no cwd")}}</div>
          <div class="subtle">messages: ${{item.message_count}}${{item.pending_approval ? " · pending approval" : ""}}</div>
        `;
        button.addEventListener("click", () => setCurrentChat(item.conversation_key));
        chatListEl.appendChild(button);
      }}
    }}

    async function refreshChat() {{
      if (!state.currentConversationKey) {{
        messagesEl.innerHTML = "<div class='bubble system'>Enter a conversation key to start.</div>";
        return;
      }}
      const response = await fetch(`/api/chat?conversation_key=${{encodeURIComponent(state.currentConversationKey)}}${{querySuffix ? "&" + querySuffix.slice(1) : ""}}`);
      if (!response.ok) {{
        messagesEl.innerHTML = "<div class='bubble system'>Conversation not found yet.</div>";
        return;
      }}
      const payload = await response.json();
      state.currentConversationKey = payload.conversation_key;
      conversationInputEl.value = payload.conversation_key;
      chatTitleEl.textContent = `${{payload.channel}} · ${{payload.chat_id}}`;
      chatMetaEl.textContent = `key: ${{payload.conversation_key}} · cwd: ${{payload.cwd || "unknown"}} · session: ${{payload.session_id || "none"}}${{payload.pending_approval ? " · pending approval" : ""}}`;
      renderResumeTargets(payload.resume_targets || []);
      const messageKey = payload.messages.map(item => item.id).join(",");
      if (messageKey === state.lastMessageKey) {{
        return;
      }}
      state.lastMessageKey = messageKey;
      messagesEl.innerHTML = payload.messages.map(item => `
        <article class="bubble ${{item.role}}">
          <div class="meta">${{escapeHtml(item.role)}} · ${{escapeHtml(item.source)}} · ${{escapeHtml(item.created_at)}}</div>
          <div>${{escapeHtml(item.text)}}</div>
        </article>
      `).join("") || "<div class='bubble system'>No messages yet.</div>";
      messagesEl.scrollTop = messagesEl.scrollHeight;
      refreshChatList();
    }}

    composerEl.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const prompt = promptInputEl.value.trim();
      const conversationKey = conversationInputEl.value.trim();
      if (!conversationKey) {{
        alert("Enter a conversation key first.");
        return;
      }}
      if (!prompt) {{
        return;
      }}
      await fetch("/api/chat/send" + querySuffix, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          conversation_key: conversationKey,
          prompt,
          mirror_to_telegram: mirrorToggleEl.checked,
        }}),
      }});
      promptInputEl.value = "";
      if (state.currentConversationKey !== conversationKey) {{
        setCurrentChat(conversationKey);
        return;
      }}
      await refreshChat();
    }});

    conversationInputEl.addEventListener("change", () => {{
      const value = conversationInputEl.value.trim();
      if (value) {{
        setCurrentChat(value);
      }}
    }});

    refreshChatList();
    setInterval(refreshChatList, 4000);
    setInterval(refreshChat, 2000);
  </script>
</body>
</html>"""
