from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class ContextManager:
    def __init__(self, file_path: Path | str | None = None):
        self.file_path = Path(file_path) if file_path else Path(__file__).parent.parent / "conversations.json"
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.file_path.exists():
            return
        try:
            with open(self.file_path) as f:
                self._data = json.load(f)
        except json.JSONDecodeError:
            self._data = {}

    def _save(self) -> None:
        with open(self.file_path, "w") as f:
            json.dump(self._data, f, indent=2)

    def save_chat_ctx(self, phone_number: str, chat_ctx_dict: dict[str, Any]) -> None:
        self._data[phone_number] = {
            "chat_ctx": chat_ctx_dict,
            "updated_at": datetime.now().isoformat(),
        }
        self._save()

    def get_chat_ctx_dict(self, phone_number: str) -> dict[str, Any] | None:
        entry = self._data.get(phone_number)
        return entry.get("chat_ctx") if entry else None

    def clear(self, phone_number: str) -> None:
        if phone_number in self._data:
            del self._data[phone_number]
            self._save()
