# Preserve Session On Model And Effort Switch

## Goal

Changing a chat's model or reasoning effort should not clear the current conversation context. Users expect `/model` and `/effort` to adjust future runner options while keeping the active session available for resume.

## Current Behavior

`BridgeCore._dispatch_model_command()` and `BridgeCore._dispatch_effort_command()` currently update the per-chat override and then call `SessionStore.clear()`. The UI messages also state that the old session was cleared. This affects both explicit values and `default` resets.

## Design

Use the minimal behavior change:

- `/model <model>` stores the model override and keeps the current session.
- `/model default` clears only the model override and keeps the current session.
- `/effort <effort>` stores the effort override and keeps the current session.
- `/effort default` clears only the effort override and keeps the current session.
- Pending approvals are still cleared when model or effort changes, so stale approval prompts do not carry across a changed execution profile.

Commands whose purpose is to change session identity or execution location keep their existing behavior. `/clear` still clears the session. `/project` and `/project default` still clear the session because a stored session is tied to a working directory.

## User-Facing Text

Update localized model and effort messages so they no longer claim that the old session was cleared. The messages should say that the current session is kept.

## Testing

Add focused tests around `BridgeCore` command handling:

- switching model preserves an existing session record
- resetting model to default preserves an existing session record
- switching effort preserves an existing session record
- resetting effort to default preserves an existing session record

Existing behavior for `/clear` and `/project` does not need new coverage for this change.
