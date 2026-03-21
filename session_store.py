from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SessionRecord:
    session_id: str
    cwd: str
    updated_at: str


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, SessionRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._data = {key: SessionRecord(**value) for key, value in raw.items()}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(value) for key, value in self._data.items()}
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def get(self, chat_id: int) -> SessionRecord | None:
        with self._lock:
            return self._data.get(str(chat_id))

    def set(self, chat_id: int, session_id: str, cwd: str) -> SessionRecord:
        with self._lock:
            record = SessionRecord(session_id=session_id, cwd=cwd, updated_at=_utc_now_iso())
            self._data[str(chat_id)] = record
            self._save()
            return record

    def clear(self, chat_id: int) -> bool:
        with self._lock:
            removed = self._data.pop(str(chat_id), None)
            if removed is None:
                return False
            self._save()
            return True

    def items(self) -> list[tuple[str, SessionRecord]]:
        with self._lock:
            return sorted(self._data.items(), key=lambda item: item[0])
