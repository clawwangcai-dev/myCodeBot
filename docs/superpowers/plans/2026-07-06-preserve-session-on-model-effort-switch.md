# Preserve Session On Model And Effort Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the active chat session when users switch `/model` or `/effort`.

**Architecture:** `BridgeCore` owns command dispatch for `/model` and `/effort`; the implementation changes only those command handlers and localized response text. `ModelStore` continues to persist per-chat model and effort overrides, while `SessionStore` remains untouched for these commands.

**Tech Stack:** Python 3, `unittest`, existing repository stores (`SessionStore`, `ModelStore`, `ApprovalState`), existing `BridgeCore` command dispatch.

## Global Constraints

- `/model <model>` stores the model override and keeps the current session.
- `/model default` clears only the model override and keeps the current session.
- `/effort <effort>` stores the effort override and keeps the current session.
- `/effort default` clears only the effort override and keeps the current session.
- Pending approvals are still cleared when model or effort changes.
- `/clear`, `/project`, and `/project default` keep their existing session-clearing behavior.
- User-facing model and effort messages must not claim that the old session was cleared.

---

### Task 1: Preserve Session For Model And Effort Commands

**Files:**
- Modify: `tests/test_construction_agent.py`
- Modify: `bridge_core.py`

**Interfaces:**
- Consumes: `BridgeCore.process_text(conversation: ConversationRef, text: str) -> None`
- Consumes: `SessionStore.set(chat_id: str | int, session_id: str, cwd: str, *, channel: str = DEFAULT_CHANNEL) -> SessionRecord`
- Consumes: `SessionStore.get(chat_id: str | int, *, channel: str = DEFAULT_CHANNEL) -> SessionRecord | None`
- Produces: `_dispatch_model_command()` and `_dispatch_effort_command()` no longer clear the active `SessionStore` record.

- [ ] **Step 1: Add failing tests**

Add this test method to `ConstructionAgentTest` in `tests/test_construction_agent.py`:

```python
    def test_model_and_effort_switch_preserve_session(self) -> None:
        settings = self._make_settings(
            provider="codex",
            codex_available_models=["gpt-5.5", "gpt-5.4"],
            codex_available_efforts=["low", "high"],
            codex_model="gpt-5.5",
            codex_effort="low",
        )
        store = SessionStore(self.tmp_path / "sessions-model-effort.json")
        workdirs = WorkdirStore(self.tmp_path / "chat_workdirs-model-effort.json")
        approvals = ApprovalState(self.tmp_path / "approval-model-effort.json")
        chat_log = ChatLogStore(self.tmp_path / "chat-model-effort.json")
        transport = DummyTransport()
        conversation = ConversationRef(channel="telegram", chat_id="100")
        store.set(conversation.key, session_id="existing-session", cwd=str(self.tmp_path))
        core = BridgeCore(
            settings,
            store,
            DummyRunner(),
            MediaHandler(settings),
            BridgeRuntimeState(),
            {
                "provider": "codex",
                "git_commit": "test",
                "claude_version": "n/a",
                "codex_version": "n/a",
                "copilot_version": "n/a",
                "transcription_backend": "n/a",
                "faster_whisper_version": "n/a",
                "whisper_bin": "n/a",
                "whisper_resolved": "n/a",
                "python": "test",
                "platform": "test",
                "claude_bin": "n/a",
                "codex_bin": "n/a",
                "copilot_bin": "n/a",
            },
            approvals,
            workdirs,
            chat_log,
            None,
            self.service,
            transport,
        )

        core.process_text(conversation, "/model gpt-5.4")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")
        core.process_text(conversation, "/model default")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")
        core.process_text(conversation, "/effort high")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")
        core.process_text(conversation, "/effort default")
        self.assertEqual(store.get(conversation.key).session_id, "existing-session")
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_construction_agent.ConstructionAgentTest.test_model_and_effort_switch_preserve_session -v
```

Expected: FAIL because `store.get(conversation.key)` becomes `None` after the first `/model` command.

- [ ] **Step 3: Keep sessions in model command handler**

In `bridge_core.py`, update `_dispatch_model_command()` by deleting the two `self._store.clear(conversation.key)` calls in the `default/reset` branch and the explicit model branch. The resulting branches must be:

```python
        if requested.lower() in {"default", "reset"}:
            self._models.clear(conversation.key)
            self._approvals.clear(conversation.key)
            self._send_message(conversation, self.render_ui_text(conversation, "model_reset"))
            return
```

and:

```python
        self._models.set(conversation.key, requested)
        self._approvals.clear(conversation.key)
        self._send_message(
            conversation,
            self.render_ui_text(conversation, "model_switched", model=requested),
        )
```

- [ ] **Step 4: Keep sessions in effort command handler**

In `bridge_core.py`, update `_dispatch_effort_command()` by deleting the two `self._store.clear(conversation.key)` calls in the `default/reset` branch and the explicit effort branch. The resulting branches must be:

```python
        if requested in {"default", "reset"}:
            self._efforts.clear(conversation.key)
            self._approvals.clear(conversation.key)
            self._send_message(conversation, self.render_ui_text(conversation, "effort_reset"))
            return
```

and:

```python
        self._efforts.set(conversation.key, requested)
        self._approvals.clear(conversation.key)
        self._send_message(
            conversation,
            self.render_ui_text(conversation, "effort_switched", effort=requested),
        )
```

- [ ] **Step 5: Update localized user-facing text**

In `bridge_core.py`, replace the six `model_switched`, `model_reset`, `effort_switched`, and `effort_reset` strings that mention clearing sessions.

Use these Chinese strings:

```python
        "model_switched": "已将当前 chat 的模型切换为 {model}，并保留当前会话。",
        "model_reset": "已恢复默认模型，并保留当前会话。",
        "effort_switched": "已将当前 chat 的思考深度切换为 {effort}，并保留当前会话。",
        "effort_reset": "已恢复默认思考深度，并保留当前会话。",
```

Use these German strings:

```python
        "model_switched": "Modell fuer diesen Chat auf {model} umgestellt; aktuelle Sitzung bleibt erhalten.",
        "model_reset": "Standardmodell wiederhergestellt; aktuelle Sitzung bleibt erhalten.",
        "effort_switched": "Effort fuer diesen Chat auf {effort} umgestellt; aktuelle Sitzung bleibt erhalten.",
        "effort_reset": "Standard-Effort wiederhergestellt; aktuelle Sitzung bleibt erhalten.",
```

Use these English strings:

```python
        "model_switched": "Switched this chat to model {model} and kept the current session.",
        "model_reset": "Restored the default model and kept the current session.",
        "effort_switched": "Switched this chat to effort {effort} and kept the current session.",
        "effort_reset": "Restored the default effort and kept the current session.",
```

- [ ] **Step 6: Run focused test**

Run:

```bash
python3 -m unittest tests.test_construction_agent.ConstructionAgentTest.test_model_and_effort_switch_preserve_session -v
```

Expected: PASS.

- [ ] **Step 7: Run broader verification**

Run:

```bash
python3 -m unittest tests.test_construction_agent -v
python3 -m py_compile *.py
```

Expected: all tests pass and `py_compile` exits with code 0.

- [ ] **Step 8: Restart bridge service**

Run:

```bash
systemctl --user restart telegram-claude-bridge.service
sleep 1
systemctl --user status telegram-claude-bridge.service --no-pager
```

Expected: service is `active (running)` and logs show both `claude_bot` and `codex_bot` workers started.

- [ ] **Step 9: Commit only relevant files**

Run:

```bash
git add bridge_core.py tests/test_construction_agent.py docs/superpowers/plans/2026-07-06-preserve-session-on-model-effort-switch.md
git commit -m "Preserve sessions when switching model or effort"
```

Do not stage runtime files under `.claude/`.
